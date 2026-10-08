"""Role-safe hosting suggestions, commitments, and explicit publication requests."""

import asyncio
import json
import logging
import time
from dataclasses import dataclass

import discord

from rionnag import config
from rionnag.scrims.scrims import Player, make_team, normalized_roles
from rionnag.services.scrim_availability import covers_interval

log = logging.getLogger(__name__)
MARKER = "Rionnag scrim hosting board"


@dataclass
class HostSlot:
    start: int
    end: int
    available: set
    confirmed: set
    lineup: dict
    secondary: int
    ready: bool

    @property
    def order(self):
        return (not self.ready, self.secondary, -len(self.confirmed), self.start)


def role_team(players):
    roster = []
    for player in players:
        answers = player["answers"]
        roles = normalized_roles([answers.get("preferred_role_1"), answers.get("preferred_role_2")])
        if roles:
            roster.append(Player(player["member_id"], "", roles))
    try:
        team = make_team(roster)
    except ValueError:
        return {}, 0
    primary = {p.member_id: p.roles[0] for p in roster}
    return team, sum(role != primary[mid] for mid, role in team.items())


def generate_slots(players, votes, now, duration=7200, days=14):
    """Only show full-interval, best/secondary-only 2–2–2 compositions."""
    players = list({p["member_id"]: p for p in players}.values())
    first = (int(now) // 1800 + 1) * 1800
    result = []
    for start in range(first, int(now) + days * 86400, 1800):
        available = [p for p in players if covers_interval(p["answers"], start, start + duration)]
        if len(available) < 6:
            continue
        team, secondary = role_team(available)
        if not team:
            continue
        ids = {p["member_id"] for p in available}
        confirmed = ids & votes.get(start, set())
        confirmed_team, confirmed_secondary = role_team([p for p in available if p["member_id"] in confirmed])
        result.append(
            HostSlot(
                start,
                start + duration,
                ids,
                confirmed,
                confirmed_team or team,
                confirmed_secondary if confirmed_team else secondary,
                bool(confirmed_team),
            )
        )
    return sorted(result, key=lambda slot: slot.order)


class HostingService:
    def __init__(self, bot, applications, candidates, channel_id=config.SCRIM_HOST_CHANNEL_ID):
        self.bot, self.applications, self.store = bot, applications, applications.store
        self.candidates, self.channel_id = candidates, channel_id
        self.lock = asyncio.Lock()
        self.fingerprint = None
        self.rank_cache = {}

    async def advert_ranks(self, slot):
        from rionnag.integrations.rivals import fetch_player_ranks, queued_lookup
        from rionnag.scrims.scrim_offer_rules import canonical_rank

        players = {p["member_id"]: p for p in self.players()}
        ranks = []
        for mid, role in sorted(
            slot.lineup.items(), key=lambda item: (("Tank", "DPS", "Support").index(item[1]), item[0])
        ):
            answers = players[mid]["answers"]
            uid = str(answers.get("player_uid") or "")
            fields = {}
            cached = self.rank_cache.get(uid)
            if uid and cached and cached[0] > time.time():
                fields = cached[1]
            elif uid:
                try:
                    fields = await asyncio.wait_for(
                        asyncio.to_thread(queued_lookup, fetch_player_ranks, uid), timeout=45
                    )
                    self.rank_cache[uid] = (time.time() + 600, fields)
                except Exception:
                    log.warning("Hosting rank lookup unavailable; ranks will be marked unavailable")
            ranks.append(
                tuple(
                    canonical_rank(str(fields.get(key, "")).split(" (")[0]) or "Unavailable"
                    for key in ("current_rank", "peak_rank")
                )
            )
        return tuple(ranks)

    async def configure(self, minimum, maximum, destination, minutes):
        from rionnag.cogs.scrim_hosting import posting_channels
        from rionnag.services.scrim_search import normalize_filter

        ranks = normalize_filter(minimum, maximum or None)
        if ranks is None:
            raise ValueError("Choose a specific opponent rank range.")
        if not 60 <= minutes <= 240:
            raise ValueError("Choose a session length from 60 to 240 minutes.")
        old = self.store.host_settings(self.channel_id)
        allowed = posting_channels()
        target = int(destination) if destination else old["destination"]
        if target is None and len(allowed) == 1:
            target = next(iter(allowed))
        if target not in allowed:
            raise ValueError("Choose one of the configured external scrim channels for adverts.")
        async with self.lock:
            if old["duration"] != minutes * 60:
                self.store.prune_host_votes(self.channel_id, {})
            self.store.configure_host(
                self.channel_id,
                min_rank=ranks[0],
                max_rank=ranks[1],
                destination=target,
                duration=minutes * 60,
            )
        await self.sync()

    def manager(self, interaction):
        form = self.applications.forms.get("marvel-rivals", {})
        return bool(
            interaction.guild_id == config.GUILD_ID
            and interaction.guild
            and (
                interaction.user.id == interaction.guild.owner_id
                or any(r.id == form.get("manager_role") for r in interaction.user.roles)
            )
        )

    def players(self):
        guild = self.bot.get_guild(config.GUILD_ID)
        form = self.applications.forms.get("marvel-rivals")
        if not guild or not form:
            return []
        return [
            p
            for p in self.candidates(guild, form)
            if not any(r.id == config.VISITOR_ROLE_ID for r in guild.get_member(p["member_id"]).roles)
        ]

    def snapshot(self, now=None):
        settings = self.store.host_settings(self.channel_id)
        return generate_slots(
            self.players(),
            self.store.host_votes(self.channel_id),
            time.time() if now is None else now,
            settings["duration"],
        )

    def board_embed(self, slots):
        settings = self.store.host_settings(self.channel_id)
        embed = discord.Embed(
            title="HOST SCRIMS",
            color=config.COLOR,
            description=f"Confirm sessions you can attend in full · {settings['duration'] // 60} minutes\n"
            "Next 14 days · Times display in your local time zone.\n"
            "2 Tank / 2 DPS / 2 Support · Best and second-best roles only.",
        )
        ready = [s for s in slots if s.ready]
        collecting = [s for s in slots if not s.ready]
        for title, group in (("Ready to host", ready), ("Needs confirmations", collecting)):
            if group:
                embed.add_field(
                    name=title,
                    value="\n\n".join(
                        f"{'★ ' if index == 0 and s.ready else ''}<t:{s.start}:F> – <t:{s.end}:t>\n"
                        f"**{len(s.confirmed)} confirmed** · {len(s.available)} available · "
                        f"{6 - s.secondary} best / {s.secondary} secondary"
                        for index, s in enumerate(group[:3])
                    ),
                    inline=False,
                )
        if not slots:
            embed.add_field(
                name="No qualifying sessions yet",
                value="We need six available players who can fill 2 Tank, 2 DPS and 2 Support. "
                "Check your saved schedule and roles with /edit_form.",
                inline=False,
            )
        adverts = self.store.host_adverts(self.channel_id)
        for start, advert in sorted(adverts.items()):
            if start <= time.time() or len(embed.fields) >= 8:
                continue
            slot = next((s for s in slots if s.start == start and s.end - start == advert["duration"]), None)
            roster = {int(mid): role for mid, role in json.loads(advert["lineup"]).items()}
            players = {p["member_id"]: p for p in self.players()}
            intact = bool(
                slot
                and all(
                    mid in slot.confirmed
                    and mid in players
                    and role
                    in normalized_roles(
                        [
                            players[mid]["answers"].get("preferred_role_1"),
                            players[mid]["answers"].get("preferred_role_2"),
                        ]
                    )
                    for mid, role in roster.items()
                )
            )
            state = advert["status"].replace("_", " ").capitalize()
            if advert["error"]:
                state += f" · {advert['error']}"
            if not intact:
                state += " · NEEDS REPLACEMENT / MANAGER REVIEW"
            link = (
                f"\n[View advert](https://discord.com/channels/@me/{advert['destination']}/"
                f"{advert['message_id']})"
                if advert["message_id"]
                else ""
            )
            embed.add_field(name="Selected session", value=f"<t:{start}:F> · {state}{link}", inline=False)
        embed.set_footer(text=MARKER)
        return embed

    async def sync(self):
        from rionnag.ui.scrim_hosting import HostingBoard

        async with self.lock:
            slots = self.snapshot()
            self.store.prune_host_votes(self.channel_id, {s.start: s.available for s in slots})
            settings = self.store.host_settings(self.channel_id)
            guild = self.bot.get_guild(config.GUILD_ID)
            channel = guild.get_channel(self.channel_id) if guild else None
            if channel is None:
                log.warning("Hosting board channel unavailable")
                return
            embed = self.board_embed(slots)
            fingerprint = json.dumps(embed.to_dict(), sort_keys=True)
            if fingerprint == self.fingerprint and settings["message_id"]:
                return
            message = None
            if settings["message_id"]:
                try:
                    message = await channel.fetch_message(settings["message_id"])
                except discord.NotFound:
                    pass
            if message is None:
                # Recovery before sending prevents a duplicate after interrupted delivery.
                async for candidate in channel.history(limit=100):
                    if candidate.author.id == self.bot.user.id and any(
                        e.footer.text == MARKER for e in candidate.embeds
                    ):
                        message = candidate
                        break
            if message:
                await message.edit(
                    embed=embed, view=HostingBoard(self), allowed_mentions=discord.AllowedMentions.none()
                )
            else:
                message = await channel.send(
                    embed=embed, view=HostingBoard(self), allowed_mentions=discord.AllowedMentions.none()
                )
            self.store.configure_host(self.channel_id, message_id=message.id)
            self.fingerprint = fingerprint

    async def change_votes(self, member_id, starts, add, expected_duration=None):
        async with self.lock:
            if (
                add
                and expected_duration is not None
                and expected_duration != self.store.host_settings(self.channel_id)["duration"]
            ):
                raise ValueError("Session length changed. Reopen Choose times to confirm the new duration.")
            slots = {s.start: s for s in self.snapshot()}
            if add and any(start not in slots or member_id not in slots[start].available for start in starts):
                raise ValueError(
                    "A session changed or falls outside your saved availability. Refresh or /edit_form."
                )
            for start in starts:
                self.store.set_host_vote(self.channel_id, start, member_id, add)
        await self.sync()

    async def publish(self, interaction, start, expected, rank_lines=()):
        if not self.manager(interaction):
            raise ValueError("Only the owner or Marvel Rivals Managers can publish adverts.")
        async with self.lock:
            settings = self.store.host_settings(self.channel_id)
            slot = next((s for s in self.snapshot() if s.start == start), None)
            if not slot or not slot.ready:
                raise ValueError("This session no longer has a confirmed 2–2–2 team.")
            if self.preview_token(slot, settings) != expected:
                raise ValueError("The lineup or settings changed. Review a fresh preview before publishing.")
            if not settings["destination"] or not settings["min_rank"]:
                raise ValueError("Set the destination and rank range with /scrim_host_settings first.")
            previous = self.store.host_adverts(self.channel_id).get(start)
            if previous and previous["status"] == "sent":
                old_team = {int(mid): role for mid, role in json.loads(previous["lineup"]).items()}
                if old_team == slot.lineup:
                    raise ValueError("This lineup is already selected and its advert was delivered.")
                if previous["duration"] != settings["duration"]:
                    raise ValueError(
                        "The published session has a different duration. Choose another session."
                    )
                self.store.replace_host_lineup(self.channel_id, start, slot.lineup)
            elif previous and previous["status"] == "failed":
                self.store.retry_failed_host_advert(self.channel_id, start)
                self.store.queue_host_advert(
                    self.channel_id, start, settings, slot.lineup, interaction.user.id, rank_lines
                )
            elif not self.store.queue_host_advert(
                self.channel_id, start, settings, slot.lineup, interaction.user.id, rank_lines
            ):
                raise ValueError(
                    "This session already has a publication request. Check its status on the board."
                )
        await self.sync()

    @staticmethod
    def preview_token(slot, settings):
        return (
            tuple(sorted(slot.lineup.items())),
            settings["duration"],
            settings["destination"],
            settings["min_rank"],
            settings["max_rank"],
        )
