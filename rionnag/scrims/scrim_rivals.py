"""Rivals API adapter; retain source payloads and verify the entire scrim roster."""

from __future__ import annotations

import math
import random
import re
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

from rivals_api import RivalsClient, hero_class

from rionnag.integrations.rivals_config import client_options
from rionnag.scrims.scrims import Player, normalized_roles


class MonitorRateLimit(RuntimeError):
    def __init__(self, delay):
        self.delay = delay
        super().__init__(f"Monitoring rate limited; retry in {delay:.0f}s")


class RequestBudget:
    """Pace every monitoring HTTP request, including SDK retries and providers.

    Only used from worker threads. One budget is shared across all scrims.
    """

    def __init__(self, interval=5):
        self.interval = max(5, interval)
        self.next_at = 0
        self.cooldown_until = 0
        self.lock = threading.Lock()
        self.identities = {}

    def wrap(self, request):
        def paced(*args, **kwargs):
            with self.lock:
                remaining = self.cooldown_until - time.monotonic()
                if remaining > 0:
                    raise MonitorRateLimit(remaining)
                time.sleep(max(0, self.next_at - time.monotonic()))
                self.next_at = time.monotonic() + self.interval
                response = request(*args, **kwargs)
                if response.status_code == 429:
                    retry = response.headers.get("Retry-After", "60")
                    try:
                        delay = float(retry)
                    except (TypeError, ValueError):
                        try:
                            delay = parsedate_to_datetime(retry).timestamp() - time.time()
                        except (TypeError, ValueError, OverflowError):
                            delay = 60
                    delay = max(60, delay) if math.isfinite(delay) else 60
                    self.next_at = time.monotonic() + delay
                    self.cooldown_until = self.next_at
                    # Do not let SDK fallback hide a 429 or fan out to other providers.
                    raise MonitorRateLimit(delay)
                return response

        return paced


@contextmanager
def monitoring_client(budget, *, enrich=False):
    options = {**client_options(), "enrich": enrich, "use_browser_fallback": False}
    with RivalsClient(**options) as client:
        original_post = client._post_json

        def post_json(path, payload):
            # The SDK's history resource stores UID as text; Rivals Data requires i64.
            if path in {"/player/matches", "/player/matches/cached"}:
                payload = {**payload, "uid": int(payload["uid"])}
            return original_post(path, payload)

        client._post_json = post_json
        for session in (client.session, client.providers.rt.session, client.providers.tracker.session):
            session.get = budget.wrap(session.get)
            session.post = budget.wrap(session.post)
        yield client


def basic_match(value):
    """Keep match values and unknown stats, removing provenance and bot bookkeeping."""
    if isinstance(value, dict):
        reference = {"id", "name", "is_known"}.issubset(value)
        return {
            key: basic_match(item)
            for key, item in value.items()
            if key != "provider_metadata"
            and not key.startswith("_scrim_")
            and not (reference and key in {"source", "is_known", "alternatives"})
        }
    if isinstance(value, list):
        return [basic_match(item) for item in value]
    return value


def custom_game(*sources):
    """Return True/False only for explicit mode/status evidence; unknown is None."""
    for source in sources:
        if not isinstance(source, dict):
            continue
        if source.get("game_play_mode_id") in (300, "300"):
            return True
        for key in ("game_mode_id", "mode_id", "game_mode", "mode", "status"):
            value = source.get(key)
            if isinstance(value, str):
                value = value.casefold().replace("_", " ").replace("-", " ").strip()
                negative = {"not", "ended", "finished", "offline", "lobby", "waiting"}
                if "custom" in value and not negative.intersection(value.split()):
                    return True
            if key != "status" and (value == 3 or value == "3"):
                return True
            if key != "status" and value in (1, 2, "1", "2", "competitive", "quickplay", "quick match"):
                return False
    return None


def check_live(person, budget):
    with monitoring_client(budget) as client:
        identity = person.get("uid") or budget.identities.get(person["username"]) or person["username"]
        player = client.get_player(identity)
        budget.identities[person["username"]] = str(player.uid)
        status = player.raw.get("status")
        if isinstance(status, str):
            status = {"status": status}
        if not isinstance(status, dict):
            return {
                "uid": str(player.uid),
                "custom": False if "status" in player.raw and status is None else None,
                "battle_id": None,
                "payload": {"status": status},
            }
        battle_id = status.get("battle_id")
        custom = custom_game(status)
        if status.get("game_play_mode_id") in (300, "300") and not battle_id and status.get("state") != 6:
            custom = False
        payload = {"status": status}
        # Explicit custom status suffices even when /live cannot supply custom rosters.
        if battle_id and custom is None:
            live = player.live_game.fetch()
            if live is not None:
                payload["live"] = live.to_dict()
                custom = custom_game(payload["live"])
        if not battle_id and custom is None:
            # An explicit empty battle_id or idle status is evidence; omitted data is not.
            idle = str(status.get("status", "")).casefold()
            if (
                "battle_id" in status
                or status.get("state") in (4, 5)
                or idle in {"offline", "online", "idle", "in lobby", "lobby"}
            ):
                custom = False
        started_at = match_timestamp(status.get("start_time"))
        if started_at is not None and not time.time() - 86400 <= started_at <= time.time() + 60:
            started_at = None
        return {
            "uid": str(player.uid),
            "custom": custom,
            "battle_id": str(battle_id) if battle_id else None,
            "payload": payload,
            "started_at": started_at,
        }


def match_timestamp(value):
    try:
        stamp = float(value)
        if stamp > 10**12:
            stamp /= 1000
    except (TypeError, ValueError):
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            stamp = parsed.timestamp()
        except (TypeError, ValueError, OverflowError):
            return None
    return stamp if math.isfinite(stamp) else None


def same_battle(live_id, history_id, started_at, tolerance):
    if str(live_id) == str(history_id):
        return True
    # Observed API formats share the battle creation second, not the full ID.
    live = re.fullmatch(r"(\d{10}):\S+", str(live_id))
    history = re.fullmatch(r"\d+_(\d{10})_\d+_\d+_\d+", str(history_id))
    return bool(live and history and live[1] == history[1] and abs(int(live[1]) - started_at) <= tolerance)


def recent_result(match, probe, used_ids, budget, tolerance=300):
    """Inspect exactly the newest custom match; never paginate or select an older one."""
    with monitoring_client(budget, enrich=True) as client:
        test_mode = match.get("test_mode", match.get("data", {}).get("detection", {}).get("test_mode", False))
        roster = [person for person in match["roster"] if not test_mode or not person.get("simulated")]
        if not roster:
            return None
        expected = set()
        for person in roster:
            uid = person.get("uid") or budget.identities.get(person["username"])
            if not uid:
                uid = str(client.get_player(person["username"]).uid)
                budget.identities[person["username"]] = uid
            expected.add(str(uid))
        person = roster[probe % len(roster)]
        uid = person.get("uid") or budget.identities[person["username"]]
        # Construct the lazy resource directly to avoid fetching/enriching a profile every retry.
        from rivals_api.resources import PlayerMatches

        history = PlayerMatches(client, int(uid)).fetch(limit=1, mode="custom", cached=False).to_dict()
        candidates = rows(history.get("matches"))
        if not candidates:
            return None
        candidate = candidates[0]
        external_id = candidate.get("match_uid")
        stamp = match_timestamp(candidate.get("timestamp"))
        if not external_id or str(external_id) in used_ids or stamp is None:
            return None
        live_id = match.get("data", {}).get("detection", {}).get("battle_id")
        matched_battle = bool(live_id and same_battle(live_id, external_id, match["started_at"], tolerance))
        if live_id and not matched_battle:
            return None
        if custom_game(candidate) is False:
            return None
        # Provider timestamps can describe the start or finish, never the indexing time.
        if not match["started_at"] - tolerance <= stamp <= match["ended_at"] + tolerance:
            return None
        duration = candidate.get("duration_seconds")
        if duration is not None:
            try:
                duration = float(duration)
            except (ValueError, TypeError):
                return None
            if not math.isfinite(duration) or duration < 0:
                return None
            # An identified battle may end before delayed status polling detects it.
            # Without that evidence, retain the close finish-time requirement.
            if (
                not matched_battle
                and min(
                    abs(stamp - match["ended_at"]),
                    abs(stamp + duration - match["ended_at"]),
                )
                > tolerance
            ):
                return None
        # A partial detail response cached by the SDK must not stall indexing retries.
        payload = client.matches.get(external_id, refresh=True).to_dict()
        if custom_game(payload) is False or not same_team(
            payload,
            expected,
            expected_size=len(roster) if test_mode else 6,
        ):
            return None
        detail_id = payload.get("match_uid")
        if detail_id is not None and str(detail_id) != str(external_id):
            return None
        detail_stamp = match_timestamp(payload.get("timestamp"))
        if detail_stamp is not None and abs(detail_stamp - stamp) > tolerance:
            return None
        return str(external_id), basic_match(payload)


def test_pool(leaderboard, excluded_names=(), *, rng=None):
    """Sample three leaderboard accounts per role using their listed heroes.

    These are virtual queue entries, never Discord members. Inferred roles are
    only a demo approximation of preferences; the source rows remain inspectable.
    """
    candidates = list(rows(leaderboard.get("players")))
    (rng or random.SystemRandom()).shuffle(candidates)
    excluded = {name.casefold() for name in excluded_names}
    groups = {role: [] for role in ("Tank", "DPS", "Support")}
    seen = set()
    for row in candidates:
        uid, name = row.get("uid"), row.get("name")
        if not uid or not name or str(uid) in seen or name.casefold() in excluded:
            continue
        roles = normalized_roles(
            [hero_class(h.get("hero_id") if isinstance(h, dict) else h) for h in row.get("heroes", [])]
        )
        if not roles:
            continue
        # Keep each demo player in their most-played listed hero's role.
        role = roles[0]
        if len(groups[role]) >= 3:
            continue
        seen.add(str(uid))
        player = Player(-int(uid), name, (role,), str(uid), joined=len(seen), simulated=True)
        groups[role].append({**asdict(player), "leaderboard": row})
    if any(len(group) < 3 for group in groups.values()):
        raise ValueError(
            "The leaderboard did not return three identifiable players per role. Try again later."
        )
    return [player for group in groups.values() for player in group]


def fetch_test_pool(excluded_names=()):
    with RivalsClient(**client_options()) as client:
        return test_pool(client.leaderboards.fetch(limit=100, platform=1).to_dict(), excluded_names)


def rows(value):
    return list(value.values()) if isinstance(value, dict) else (value or [])


def same_team(payload, expected: set[str], *, live=False, expected_size=6) -> bool:
    if not expected or len(expected) != expected_size:
        return False
    if live:
        groups = {}
        for player in rows(payload.get("players")):
            side = player.get("team_id", player.get("side"))
            if side is not None:
                groups.setdefault(str(side), set()).add(str(player.get("uid", player.get("player_uid"))))
        return any(expected <= group for group in groups.values())
    return any(
        expected <= {str(p.get("uid", p.get("player_uid"))) for p in rows(team.get("players"))}
        for team in rows(payload.get("teams"))
    )


def resolve_roster(client, roster):
    return {str(p["uid"] or client.get_player(p["username"]).uid) for p in roster}


def discover(roster, probe, started_at, ended_at=None, used_ids=()):
    with RivalsClient(**client_options()) as client:
        expected = resolve_roster(client, roster)
        person = roster[probe % len(roster)]
        player = client.get_player(person["uid"] or person["username"])
        live = player.live_game.fetch() if ended_at is None else None
        if live:
            payload = live.to_dict()
            status = player.raw.get("status") or {}
            external_id = status.get("battle_id") if isinstance(status, dict) else None
            if external_id and str(external_id) not in used_ids and same_team(payload, expected, live=True):
                return str(external_id), "live", payload
        # Finished games can be indexed after the button is clicked or live discovery failed.
        if ended_at:
            history = player.matches.fetch(limit=10, mode="custom", cached=False)
            for candidate in rows(history.to_dict().get("matches"))[:10]:
                try:
                    stamp = float(candidate["timestamp"])
                    if stamp > 10**12:
                        stamp /= 1000
                except (KeyError, ValueError, TypeError):
                    continue
                if not started_at - 120 <= stamp <= ended_at + 60:
                    continue
                match_id = candidate.get("match_uid")
                if match_id and str(match_id) not in used_ids:
                    payload = client.matches.get(match_id).to_dict()
                    if same_team(payload, expected):
                        return str(match_id), "details", payload
    return None


def details(external_id, roster):
    with RivalsClient(**client_options()) as client:
        payload = client.matches.get(external_id).to_dict()
        if not same_team(payload, resolve_roster(client, roster)):
            raise ValueError("Match details do not contain all six selected players on the same team.")
        return payload


def scout(live):
    """Source-backed scouting; optional sections failing never erase other data."""
    result = {}
    with RivalsClient(**client_options()) as client:
        for row in rows(live.get("players")):
            uid = row.get("uid", row.get("player_uid"))
            if not uid:
                continue
            entry = {"live": row, "errors": {}}
            result[str(uid)] = entry
            try:
                player = client.get_player(uid)
                entry["profile"] = player.to_dict()
            except Exception as exc:
                entry["errors"]["profile"] = type(exc).__name__
                continue
            for name, fetch in (
                (
                    "heroes",
                    lambda: [h.to_dict() for h in player.stats.heroes(mode="competitive", season="all")],
                ),
                ("maps", lambda: [m.to_dict() for m in player.stats.maps()]),
                ("bans", lambda: [b.to_dict() for b in player.stats.bans()]),
                ("classes", lambda: player.stats.classes(season="all").to_dict()),
            ):
                try:
                    entry[name] = fetch()
                except Exception as exc:
                    entry["errors"][name] = type(exc).__name__
    return result


def scouting_lines(data):
    lines = []
    for entry in data.values():
        live = entry["live"]
        weak = []
        for hero in entry.get("heroes", []):
            record = hero.get("competitive") or {}
            wins, losses = record.get("wins"), record.get("losses")
            if wins is None or losses is None or wins + losses < 5:
                continue
            rate = wins / (wins + losses) * 100
            if rate < 50:
                weak.append((rate, hero.get("hero_name") or str(hero.get("hero_id")), wins + losses))
        weak.sort()
        evidence = "; ".join(
            f"{name}: {rate:.0f}% WR ({games} hero records)" for rate, name, games in weak[:3]
        )
        empty = "No low-win-rate hero sample." if "heroes" in entry else "Hero stats unavailable."
        lines.append((str(live.get("uid")), live.get("name", "Unknown"), evidence or empty))
    return lines
