"""Lossless local scrim archives and restart-safe Discord result delivery."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import tempfile
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import discord
from rivals_api import Match, hero_class, hero_name

from rionnag import config
from rionnag.scrims.scrim_map_images import MAP_IMAGES
from rionnag.scrims.scrim_rivals import basic_match, rows

logger = logging.getLogger(__name__)

STAT_EMOJIS = {"damage": "⚔️", "blocked": "🛡️", "healing": "💚", "accuracy": "🎯"}


def stat_emojis(guild):
    icons = dict(STAT_EMOJIS)
    for stat in icons:
        emoji = discord.utils.get(getattr(guild, "emojis", []), name=f"mr_{stat}")
        if emoji and emoji.is_usable():
            icons[stat] = str(emoji)
    return icons


def number(value):
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (int, float)) and math.isfinite(value):
        return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.2f}"
    return str(value)


def text(value):
    return discord.utils.escape_markdown(discord.utils.escape_mentions(str(value)))


def main_hero(player):
    identifier = player.get("top_hero_id")
    if identifier is None:
        heroes = rows(player.get("heroes"))
        primary = max(heroes, key=lambda h: h.get("play_time") or 0, default={})
        identifier = primary.get("hero_id")
    return identifier


def played_heroes(player):
    """Keep the provider's recorded hero order, never sort by total playtime."""
    names = []
    for hero in rows(player.get("heroes")):
        identifier = hero.get("hero_id")
        name = hero.get("hero_name") or hero_name(identifier) or hero.get("name")
        if not name and identifier is not None:
            name = f"Hero {identifier}"
        if name:
            names.append(text(name))
    return ", ".join(names) or text(
        player.get("top_hero_name") or hero_name(main_hero(player)) or "Unknown",
    )


def roster_mention(player, match):
    real = [p for p in match["roster"] if not p.get("simulated") and p.get("member_id", 0) > 0]
    uid = str(player.get("player_uid") or player.get("uid") or "")
    name = str(player.get("name") or "").casefold()
    matched = [
        p
        for p in real
        if (str(p["uid"]) == uid if p.get("uid") else bool(name) and p["username"].casefold() == name)
    ]
    return f"<@{matched[0]['member_id']}>" if len(matched) == 1 else None


def accuracy(player):
    percent = player.get("accuracy_percent")
    if isinstance(percent, (int, float)) and math.isfinite(percent):
        return f"{percent:.1f}%"
    value = player.get("accuracy")
    source = player.get("provider_metadata", {}).get("selections", {}).get("accuracy", {}).get("source")
    if value is None:
        hero = next((h for h in rows(player.get("heroes")) if h.get("hero_id") == main_hero(player)), {})
        value = hero.get("accuracy")
        source = "ratio"
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        return "—"
    if source in {"ratio", "rivalstracker", "tracker"} and 0 <= value <= 1:
        value *= 100
    return f"{value:.1f}%"


def bans(payload):
    names = []
    for entry in rows(payload.get("draft")) + rows(payload.get("bans")):
        if isinstance(entry, dict):
            if entry.get("is_pick") is True or entry.get("action") == "pick":
                continue
            identifier = entry.get("banned_hero", entry.get("hero_id", entry.get("ban_hero_id")))
            name = (
                entry.get("hero_name") or hero_name(identifier) or (str(identifier) if identifier else None)
            )
        else:
            name = hero_name(entry) or str(entry)
        if name and name not in names:
            names.append(name)
    return ", ".join(text(name) for name in names) or "None reported"


def ordered_players(team, match):
    roles = {str(p.get("uid")): p.get("role", "").lower() for p in match["roster"] if p.get("uid")}
    return sorted(
        rows(team.get("players")),
        key=lambda p: {"tank": 0, "dps": 1, "support": 2}.get(
            hero_class(main_hero(p)) or roles.get(str(p.get("player_uid") or p.get("uid"))),
            3,
        ),
    )


def compact_number(value):
    if isinstance(value, (int, float)) and math.isfinite(value) and abs(value) >= 1000:
        return f"{value / 1000:.1f}".rstrip("0").rstrip(".") + "k"
    return number(value)


def map_thumbnail(payload):
    for value in (payload.get("map_thumbnail"), payload.get("map_image_url")):
        if isinstance(value, str) and value.startswith(("https://", "http://")):
            return value
    reference = payload.get("map")
    if isinstance(reference, dict):
        for key in ("thumbnail", "image_url"):
            value = reference.get(key)
            if isinstance(value, str) and value.startswith(("https://", "http://")):
                return value
    image = MAP_IMAGES.get(str(payload.get("map_id")))
    return f"https://rivalstracker.com/images/Map/{image}.png" if image else None


def team_uids(team):
    return {str(p.get("player_uid") or p.get("uid")) for p in rows(team.get("players"))}


def match_outcome(match):
    payload = match["data"]
    teams = rows(payload.get("teams"))
    real = [p for p in match["roster"] if not p.get("simulated")]

    def contains_roster(team):
        uids = team_uids(team)
        names = {str(p.get("name", "")).casefold() for p in rows(team.get("players"))}
        return bool(real) and all(
            str(p["uid"]) in uids if p.get("uid") else p["username"].casefold() in names for p in real
        )

    ours = next((team for team in teams if contains_roster(team)), None)
    won = ours.get("is_win") if ours else None
    if not isinstance(won, bool):
        winner = payload.get("winner_camp")
        won = str(ours.get("camp")) == str(winner) if ours and winner is not None else None
    return ours, won


def result_embeds(match, session, guild=None):
    payload = match["data"]
    teams = rows(payload.get("teams"))
    ours, won = match_outcome(match)
    icons = stat_emojis(guild)
    win_color = 0x57F287
    color = win_color if won is True else 0xE74C3C if won is False else 0xF1C40F
    outcome = "WIN" if won is True else "LOSS" if won is False else "RESULT"
    summary = discord.Embed(
        title=f"Game {match['number']} · {outcome}",
        color=color,
    )
    duration = payload.get("duration_seconds")
    if isinstance(duration, (int, float)) and math.isfinite(duration) and duration >= 0:
        summary.add_field(name="Duration", value=f"{int(duration) // 60}:{int(duration) % 60:02d}")
    details = Match(payload)
    summary.add_field(
        name="Map",
        value=text(
            details.map.name
            if details.map and details.map.is_known
            else payload.get("map_name") or f"Map {payload.get('map_id', '—')}",
        ),
    )
    mode = details.gameplay_mode
    summary.add_field(name="Game mode", value=text(mode.name if mode and mode.is_known else "Unknown"))
    thumbnail = map_thumbnail(payload)
    if thumbnail:
        summary.set_image(url=thumbnail)
    ordered = sorted(teams, key=lambda team: team is not ours)
    summary.add_field(name="Score", value=" : ".join(number(team.get("round_score")) for team in ordered))
    embeds = [summary]
    for index, team in enumerate(sorted(teams, key=lambda team: team is not ours), 1):
        label = "Team Rionnag" if team is ours else "Opponent Team" if ours else f"Team {index}"
        won = team.get("is_win")
        if not isinstance(won, bool):
            winner = payload.get("winner_camp")
            won = str(team.get("camp")) == str(winner) if winner is not None else None
        color = win_color if won is True else 0xE74C3C if won is False else 0xF1C40F
        outcome = "WIN" if won is True else "LOSS" if won is False else "RESULT"
        embed = discord.Embed(title=f"{label} · {outcome}", color=color)
        for player in ordered_players(team, match):
            name = str(player.get("name") or player.get("player_uid") or player.get("uid") or "Unknown")
            name = discord.utils.escape_mentions(" ".join(name.replace("`", "'").split()))[:240]
            stats = " | ".join(
                (
                    played_heroes(player),
                    "/".join(number(player.get(key)) for key in ("kills", "deaths", "assists")),
                    number(player.get("final_hits", player.get("last_kill"))) + "F",
                    compact_number(player.get("damage")) + " " + icons["damage"],
                    compact_number(player.get("blocked")) + " " + icons["blocked"],
                    compact_number(player.get("healing")) + " " + icons["healing"],
                    accuracy(player) + " " + icons["accuracy"],
                )
            )
            star = "★ " if player.get("is_mvp") is True or player.get("is_svp") is True else ""
            mention = roster_mention(player, match) if team is ours else None
            # Mentions render in field values, not field headings.
            embed.add_field(
                name="\u200b" if mention else f"{star}`{name}`",
                value=(f"**{star}{mention}**\n{stats}" if mention else stats)[:1024],
                inline=False,
            )
        if team is ours:
            embed.add_field(name="Bans", value=bans(payload)[:1024], inline=False)
        embeds.append(embed)
    return embeds


def session_embed(session, matches):
    wins = losses = unverified = 0
    awards = Counter()
    for match in matches:
        if match["status"] != "completed":
            continue
        if not match.get("external_id"):
            unverified += 1
            continue
        ours, won = match_outcome(match)
        wins += won is True
        losses += won is False
        unverified += won is None
        if ours is None:
            continue
        # One player's provider row contributes once per game, even if duplicated.
        game_awards = {}
        for player in rows(ours.get("players")):
            mention = roster_mention(player, match)
            count = int(player.get("is_mvp") is True) + int(player.get("is_svp") is True)
            if mention and count:
                game_awards[mention] = max(game_awards.get(mention, 0), count)
        awards.update(game_awards)
    maximum = max(awards.values(), default=0)
    mvps = [mention for mention, count in awards.items() if count == maximum]
    ended_at = session.get("ended_at") or max((m.get("ended_at") or 0 for m in matches), default=0)
    embed = discord.Embed(
        title=f"Session ended · {wins}–{losses}",
        color=0x9B59B6,
        timestamp=datetime.fromtimestamp(ended_at, UTC) if ended_at else None,
    )
    embed.add_field(name="Score", value=f"{wins}–{losses}")
    embed.add_field(name="MVP(s)", value=", ".join(mvps) or "None recorded", inline=False)
    if unverified:
        embed.add_field(name="Unverified games", value=str(unverified))
    return embed


def embed_signature(embeds):
    return [
        (
            e.title,
            e.description,
            e.timestamp,
            e.image.url,
            e.color.value if e.color else None,
            [(f.name, f.value, f.inline) for f in e.fields],
        )
        for e in embeds
    ]


class ScrimResultLogs:
    def __init__(self, controller):
        self.controller = controller

    def archive(self, match):
        store = self.controller.store
        with store.connection() as db:
            database = db.execute("PRAGMA database_list").fetchone()[2]
        root = Path(os.getenv("SCRIM_ARCHIVE_DIR") or Path(database).parent / "scrim_archive").resolve()
        session = store.get(match["session_id"])
        root = root / str(session["guild_id"]) / f"session-{session['id']}"
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"game-{match['number']}-match-{match['id']}.json"
        bundle = basic_match(match["data"])
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=root, delete=False) as file:
            json.dump(bundle, file, ensure_ascii=False, indent=2, allow_nan=False)
            temporary = file.name
        os.replace(temporary, path)
        return path

    async def tick(self):
        pending = self.controller.store.pending_results()
        for match in pending:
            state = self.controller.store.result_delivery(match["id"])
            if state.get("next_at", 0) <= time.time():
                await self.publish(match, state)
                return
        for session in self.controller.store.pending_session_results():
            if any(match["session_id"] == session["id"] for match in pending):
                continue  # Deliver known game results before the session separator.
            state = self.controller.store.session_delivery(session["id"])
            if state.get("next_at", 0) <= time.time():
                await self.publish_session(session, state)
                return

    def log_channel(self, session):
        guild = self.controller.bot.get_guild(session["guild_id"])
        if guild is None:
            raise ValueError("Guild is unavailable")
        channel_id = os.getenv("SCRIM_LOG_CHANNEL_ID")
        if not channel_id and guild.id == config.GUILD_ID:
            channel_id = config.SCRIM_LOG_CHANNEL_ID
        channel = (
            guild.get_channel(int(channel_id))
            if channel_id
            else discord.utils.get(
                getattr(guild, "text_channels", []),
                name="scrim-logs",
            )
        )
        if channel is None:
            raise ValueError("The scrim-logs text channel is unavailable")
        return guild, channel

    async def publish_session(self, session, state):
        store = self.controller.store
        matches = store.session_matches(session["id"])
        if not any(match["status"] == "completed" for match in matches):
            state.update(done=True, skipped="No completed games", error=None)
            store.save_session_delivery(session["id"], state)
            return
        try:
            _, channel = self.log_channel(session)
            embeds = [session_embed(session, matches)]
            previous = state.get("attempted_embed")
            state.setdefault("first_attempt_at", time.time())
            state["attempted_embed"] = embeds[0].to_dict()
            store.save_session_delivery(session["id"], state)
            message = None
            if state.get("message_id"):
                try:
                    message = await channel.fetch_message(state["message_id"])
                except discord.NotFound:
                    pass
            if message is None:
                async for candidate in channel.history(limit=100):
                    if candidate.author.id != self.controller.bot.user.id:
                        continue
                    if candidate.created_at.timestamp() < state["first_attempt_at"] - 2:
                        continue
                    if (
                        embed_signature(candidate.embeds) == embed_signature(embeds)
                        or previous
                        and embed_signature(candidate.embeds)
                        == embed_signature(
                            [
                                discord.Embed.from_dict(previous),
                            ]
                        )
                    ):
                        message = candidate
                        break
            if message and state.get("repost"):
                await message.delete()
                message = None
                state.update(message_id=None, repost=False, first_attempt_at=time.time())
                store.save_session_delivery(session["id"], state)
            if message:
                await message.edit(embeds=embeds, allowed_mentions=discord.AllowedMentions.none())
            else:
                message = await channel.send(
                    embeds=embeds,
                    allowed_mentions=discord.AllowedMentions.none(),
                    nonce=f"ss{session['id']}",
                )
            state.update(message_id=message.id, channel_id=channel.id, done=True, error=None)
            store.save_session_delivery(session["id"], state)
            logger.info("Posted scrim session %s summary", session["id"])
        except Exception as exc:
            state.update(error=f"{type(exc).__name__}: {exc}", next_at=time.time() + 60)
            store.save_session_delivery(session["id"], state)
            logger.exception("Scrim session %s summary delivery will retry", session["id"])

    async def publish(self, match, state):
        store = self.controller.store
        try:
            # Save everything locally before attempting any Discord operation.
            path = await asyncio.to_thread(self.archive, match)
            state["archive_path"] = str(path)
            state.setdefault("first_attempt_at", time.time())
            store.save_result_delivery(match["id"], state)
            session = store.get(match["session_id"])
            guild, channel = self.log_channel(session)
            pages = []
            batch = []
            for embed in result_embeds(match, session, guild):
                if batch and (len(batch) >= 10 or sum(len(e) for e in batch) + len(embed) + 300 > 6000):
                    pages.append(batch)
                    batch = []
                batch.append(embed)
            if batch:
                pages.append(batch)
            # Recover sends that reached Discord just before a process interruption.
            found = {}
            async for message in channel.history(limit=100):
                if message.author.id != self.controller.bot.user.id:
                    continue
                if message.created_at.timestamp() < state["first_attempt_at"] - 2:
                    continue
                for index, embeds in enumerate(pages, 1):
                    if embed_signature(message.embeds) == embed_signature(embeds):
                        found[index] = message.id
            for index, embeds in enumerate(pages, 1):
                if index <= len(state["message_ids"]):
                    if state.get("format_version") == 7:
                        continue
                    try:
                        message = await channel.fetch_message(state["message_ids"][index - 1])
                        await message.edit(
                            embeds=embeds,
                            attachments=[],
                            allowed_mentions=discord.AllowedMentions.none(),
                        )
                        continue
                    except discord.NotFound:
                        message = await channel.send(
                            embeds=embeds,
                            allowed_mentions=discord.AllowedMentions.none(),
                        )
                        state["message_ids"][index - 1] = message.id
                        store.save_result_delivery(match["id"], state)
                        continue
                if index in found:
                    state["message_ids"].append(found[index])
                else:
                    message = await channel.send(
                        embeds=embeds,
                        allowed_mentions=discord.AllowedMentions.none(),
                        nonce=f"sr{match['id']}p{index}",
                    )
                    state["message_ids"].append(message.id)
                state["channel_id"] = channel.id
                store.save_result_delivery(match["id"], state)
            for message_id in state["message_ids"][len(pages) :]:
                try:
                    message = await channel.fetch_message(message_id)
                    await message.delete()
                except discord.NotFound:
                    pass
            state["message_ids"] = state["message_ids"][: len(pages)]
            state.update(done=True, error=None, format_version=7)
            store.save_result_delivery(match["id"], state)
            logger.info("Archived scrim match %s and posted %s result pages", match["id"], len(pages))
        except Exception as exc:
            state.update(error=f"{type(exc).__name__}: {exc}", next_at=time.time() + 60)
            store.save_result_delivery(match["id"], state)
            logger.exception("Scrim result %s delivery will retry; local data retained", match["id"])
