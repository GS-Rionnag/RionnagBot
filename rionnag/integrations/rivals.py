from __future__ import annotations

import logging
import threading
import time

import discord
from rivals_api import RivalsAPIError, RivalsClient

from .rivals_config import client_options

logger = logging.getLogger(__name__)
lookup_lock = threading.Lock()
lookup_next_at = 0.0


def queued_lookup(function, *args):
    """Serialize provider work, spacing requests and backing off on HTTP 429."""
    global lookup_next_at
    with lookup_lock:
        for attempt in range(4):
            time.sleep(max(0, lookup_next_at - time.monotonic()))
            try:
                return function(*args)
            except Exception as exc:
                response = getattr(exc, "response", None)
                status = getattr(exc, "status_code", None) or getattr(response, "status_code", None)
                if attempt == 3 or not (
                    status == 429 or "429" in str(exc) or "rate limit" in str(exc).lower()
                ):
                    raise
                headers = getattr(response, "headers", {}) or {}
                try:
                    delay = float(headers.get("Retry-After", 2 ** (attempt + 1)))
                except (ValueError, TypeError):
                    delay = 2 ** (attempt + 1)
                lookup_next_at = time.monotonic() + min(60, max(1, delay))
            finally:
                lookup_next_at = max(lookup_next_at, time.monotonic() + 1)


def search_player_accounts(username: str) -> list[dict]:
    with RivalsClient(**client_options()) as client:
        return [
            {"uid": str(row.uid), "name": row.name or str(row.uid)} for row in client.search_players(username)
        ]


def fetch_player_overview(username: str, method: str | None = None) -> dict:
    """Use canonical rates, preserving the SDK default unless a method is selected."""
    if method not in {None, "normal", "precise"}:
        raise ValueError("Choose normal or precise lookup.")
    rate_options = {"season": "current"}
    if method is not None:
        rate_options["method"] = method
    with RivalsClient(**client_options()) as client:
        player = client.get_player(username)
        hero_ranks = {}
        rank_errors = False
        # Fetch the separate leaderboard metadata before detail-heavy canonical stats.
        for mode in ("competitive", "quickplay"):
            try:
                for hero in player.stats.summary_heroes(mode=mode, season="all"):
                    if hero.rank is not None:
                        hero_ranks.setdefault(str(hero.hero_id), hero.rank)
            except RivalsAPIError:
                rank_errors = True
                logger.warning("Hero leaderboard lookup unavailable for %s mode", mode)
        rates = {"overall": None, "heroes": None, "classes": None}
        errors = []
        overall_scope = "Current Season"
        for name, fetch in (
            ("overall", lambda: player.stats.win_rate(**rate_options)),
            ("heroes", lambda: player.stats.hero_win_rates(**rate_options)),
            ("classes", lambda: player.stats.class_win_rates(**rate_options)),
        ):
            try:
                rates[name] = fetch().to_dict()
                metadata = rates[name].get("metadata", {})
                if metadata.get("provider_errors"):
                    errors.append(f"{name.capitalize()} provider coverage is partial.")
                if (
                    metadata.get("unresolved")
                    or metadata.get("selection_uncertain")
                    or metadata.get("coverage", {}).get("unknown_results")
                ):
                    errors.append(f"Some {name} outcomes or hero attributions are unresolved.")
            except (RivalsAPIError, ValueError):
                logger.exception("Couldn't fetch canonical %s rates", name)
                errors.append(f"{name.capitalize()} win rates are temporarily unavailable.")
        if rank_errors:
            errors.append("Some hero leaderboard placements are temporarily unavailable.")
        overall = rates["overall"] or {}
        overall_rate = overall.get("win_rate_pct")
        partial = overall.get("partial_result")
        if overall_rate is None and isinstance(partial, dict) and partial.get("win_rate_pct") is not None:
            overall_rate = partial["win_rate_pct"]
            available = ", ".join(partial.get("included_modes", []))
            errors.append(f"Overall win rate covers available modes only ({available}); other modes are unavailable.")
        return {
            "player_uid": str(player.uid),
            "player_name": player.name or str(player.uid),
            "win_rate": overall_rate,
            "top_heroes": [],
            "rank_game_season": player.rank_game_season or {},
            "match_hero_rates": rates["heroes"]["data"] if rates["heroes"] is not None else None,
            "match_class_rates": rates["classes"]["data"] if rates["classes"] is not None else None,
            "hero_ranks": hero_ranks,
            "stats_mode": "all",
            "overall_scope": overall_scope,
            "hero_scope": "Current Season",
            "win_rate_note": " ".join(errors),
        }


RANK_TIERS = (
    "Bronze",
    "Silver",
    "Gold",
    "Platinum",
    "Diamond",
    "Grandmaster",
    "Celestial",
    "Eternity",
    "One Above All",
)
RANK_ICON_URLS = {
    tier: (
        f"https://rivalskins.com/wp-content/uploads/marvel-assets/assets/rank-logos/"
        f"{index}%20{tier.replace(' ', '%20')}%20Rank.png"
    )
    for index, tier in enumerate(RANK_TIERS, start=1)
}


def rank_details(score: int | float | None, *, top_rank: bool = False) -> tuple[str, str | None]:
    """Return the ranked tier and its logo from the rank score."""
    if score is None:
        return "Unranked", None
    value = float(score)
    thresholds = (
        (5100, "Eternity"),
        (4800, "Celestial"),
        (4500, "Grandmaster"),
        (4200, "Diamond"),
        (3900, "Platinum"),
        (3600, "Gold"),
        (3300, "Silver"),
        (3000, "Bronze"),
    )
    tier = next((name for threshold, name in thresholds if value >= threshold), "Bronze")
    if value >= 5100 and top_rank:
        tier = "One Above All"
    icon = RANK_ICON_URLS[tier]
    return (f"{tier} ({value:g} points)", icon)


def profile_overview(profile: dict) -> tuple[dict[str, str], str | None]:
    """Format profile data for separate Discord embed fields."""
    rows = profile["rank_game_season"]
    competitive = [row for account, row in rows.items() if str(account).startswith("1001")]
    if not competitive:
        competitive = list(rows.values())
    latest = max(competitive, key=lambda row: int(getattr(row, "rank_game_id", 0) or 0), default=None)
    current_score = getattr(latest, "rank_score", None) if latest else None
    current_rank, _ = rank_details(current_score)
    peak_row = max(
        competitive,
        key=lambda row: float(getattr(row, "max_rank_score", None) or getattr(row, "rank_score", 0) or 0),
        default=None,
    )
    peak_score = (
        getattr(peak_row, "max_rank_score", None) or getattr(peak_row, "rank_score", None)
        if peak_row
        else None
    )
    peak_level = (
        getattr(peak_row, "max_level", None) or getattr(peak_row, "season_max_level", None)
        if peak_row
        else None
    )
    peak_rank, peak_icon = rank_details(peak_score, top_rank=bool(peak_level and int(peak_level) >= 23))

    character_rows = []
    for hero in profile["top_heroes"]:
        hero_id = str(hero.hero_id)
        name = (
            "Deadpool"
            if hero_id in {"10571", "10572", "10573"}
            else hero.hero_name or getattr(hero, "name", None) or f"Hero {hero_id}"
        )
        rate = getattr(hero, "win_rate", None)
        rate_text = f"{rate}% win rate" if rate is not None else "win rate unavailable"
        character_rows.append(f"**{discord.utils.escape_markdown(name)}:** {rate_text}")
    characters = "\n".join(character_rows) if character_rows else "No public hero stats."

    class_rows = []
    for row in profile.get("class_stats", []):
        rate = row.competitive.win_rate
        rate_text = f"{rate}%" if rate is not None else "N/A"
        class_rows.append(f"**{discord.utils.escape_markdown(row.role)}:** {rate_text}")
    classes = "\n".join(class_rows) if class_rows else "No public class stats."

    if "match_class_rates" in profile:
        class_rows = []
        roles = {"tank": "Vanguard", "support": "Strategist", "dps": "Duelist"}
        for row in profile["match_class_rates"] or []:
            role = roles.get(row["player_class"], row["player_class"])
            rate = row.get("win_rate_pct")
            class_rows.append(
                f"**{discord.utils.escape_markdown(role)}:** {rate:g}%"
                if rate is not None
                else f"**{discord.utils.escape_markdown(role)}:** N/A"
            )
        classes = "\n".join(class_rows) or (
            "Stats temporarily unavailable."
            if profile["match_class_rates"] is None
            else "No public class match history."
        )

    overview = {
        "overall_win_rates_name": f"{profile.get('overall_scope', 'Current Season')} Win Rate",
        "overall_win_rate": f"{profile['win_rate']:g}%" if profile["win_rate"] is not None else "Unavailable",
        "current_rank": current_rank,
        "peak_rank": peak_rank,
        "top_characters": characters,
        "role_win_rates_name": (
            f"Class Win Rates ({profile.get('hero_scope', 'All Seasons')})"
            if profile.get("stats_mode") == "all"
            else "Quick Play Class Win Rates (All Seasons)"
            if profile.get("stats_mode") == "quickplay"
            else "Competitive Class Win Rates (All Seasons)"
        ),
        "hero_win_rates_name": (
            f"Top 6 Characters ({profile.get('hero_scope', 'All Seasons')})"
            if profile.get("stats_mode") == "all"
            else "Top 6 Quick Play Characters (All Seasons)"
            if profile.get("stats_mode") == "quickplay"
            else "Top 6 Competitive Characters (All Seasons)"
        ),
        "role_win_rates": classes,
        "win_rate_note": profile.get("win_rate_note", ""),
    }
    if "match_hero_rates" in profile:
        lines = []
        for hero in sorted(
            profile["match_hero_rates"] or [], key=lambda row: -row.get("games", row.get("matches", 0))
        )[:6]:
            name = hero.get("hero_name") or f"Hero {hero['hero_id']}"
            rate = hero.get("win_rate_pct")
            parts = [f"{rate:g}% WR" if rate is not None else "WR unavailable"]
            rank = profile.get("hero_ranks", {}).get(str(hero["hero_id"]))
            if rank is not None:
                parts.append(f"`#{rank}`")
            lines.append(f"**{discord.utils.escape_markdown(name)}:** " + " ".join(parts))
        # Retain complete lines within Discord's field limit.
        value = ""
        for line in lines:
            candidate = f"{value}\n{line}" if value else line
            if len(candidate) > 1024:
                break
            value = candidate
        overview["competitive_heroes"] = value or (
            "Stats temporarily unavailable."
            if profile["match_hero_rates"] is None
            else "No public match history for this mode."
        )
    for mode, heroes in profile.get("heroes_by_mode", {}).items():
        lines = []
        for hero in heroes or []:
            stats = getattr(hero, mode)
            name = hero.hero_name or f"Hero {hero.hero_id}"
            rate = stats.win_rate
            parts = [f"{rate}% WR" if rate is not None else "WR unavailable"]
            if hero.rank is not None:
                parts.append(f"`#{hero.rank}`")
            lines.append(f"**{discord.utils.escape_markdown(name)}:** " + " ".join(parts))
        # Discord limits each field to 1024 characters; retain only complete rows.
        value = ""
        for line in lines:
            candidate = f"{value}\n{line}" if value else line
            if len(candidate) > 1024:
                break
            value = candidate
        overview[f"{mode}_heroes"] = value or (
            "Stats temporarily unavailable." if heroes is None else "No public hero stats."
        )
    return overview, peak_icon


def add_hero_fields(embed: discord.Embed, overview: dict[str, str]) -> None:
    if "competitive_heroes" in overview:
        embed.add_field(
            name=overview["hero_win_rates_name"], value=overview["competitive_heroes"], inline=False
        )
        embed.add_field(name=overview["role_win_rates_name"], value=overview["role_win_rates"], inline=False)
        return
    for mode, label in (("competitive", "Competitive"),):
        key = f"{mode}_heroes"
        if key in overview:
            embed.add_field(name=f"Top 6 {label} Characters (All Seasons)", value=overview[key], inline=False)
    if "competitive_heroes" not in overview:
        embed.add_field(name="Top 6 Characters", value=overview["top_characters"], inline=False)
