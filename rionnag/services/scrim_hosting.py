"""Role-safe hosting suggestions, commitments, and explicit publication requests."""

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from types import SimpleNamespace

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
        return (-len(self.confirmed), -len(self.available), self.secondary, self.start)


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
        self.notice_lock = asyncio.Lock()

    def register_approvals(self):
        from rionnag.ui.scrim_hosting import HostApproval

        for start, notice in self.store.host_notices(self.channel_id).items():
            if notice["status"] == "pending" and notice["message_id"]:
                self.bot.add_view(
                    HostApproval(self, start, notice["generation"]), message_id=notice["message_id"]
                )

    async def notify_ready(self):
        from rionnag.ui.scrim_hosting import HostApproval

        async with self.notice_lock:
            guild = self.bot.get_guild(config.GUILD_ID)
            if guild is None:
                return
            slots = {s.start: s for s in self.snapshot() if s.ready}
            notices = self.store.host_notices(self.channel_id)
            adverts = self.store.host_adverts(self.channel_id)
            for start, notice in notices.items():
                if start not in slots and notice["status"] in {"pending", "declined"}:
                    self.store.update_host_notice(self.channel_id, start, notice["generation"], "expired")
            needed = [
                slot
                for start, slot in slots.items()
                if start not in adverts
                and (
                    start not in notices
                    or notices[start]["status"] == "expired"
                    or (notices[start]["status"] == "pending" and not notices[start]["message_id"])
                )
            ]
            if not needed:
                return
            owner = guild.owner or await self.bot.fetch_user(guild.owner_id)
            dm = owner.dm_channel or await owner.create_dm()
            for slot in sorted(needed, key=lambda s: s.order):
                generation = self.store.reserve_host_notice(self.channel_id, slot.start)
                marker = f"Scrim host approval · {slot.start} · {generation}"
                # A pending row precedes delivery. Recover an interrupted send before sending again.
                recovered = None
                async for message in dm.history(limit=100):
                    if message.author.id == self.bot.user.id and any(
                        embed.footer.text == marker for embed in message.embeds
                    ):
                        recovered = message
                        break
                view = HostApproval(self, slot.start, generation)
                embed = discord.Embed(
                    title="Six players confirmed for a scrim",
                    color=config.COLOR,
                    description=f"<t:{slot.start}:F> – <t:{slot.end}:t>\n"
                    f"**{len(slot.confirmed)} players confirmed** · Valid 2 Tank / 2 DPS / 2 Support\n\n"
                    "Would you like to send a message to the scrim advertisement area?\n"
                    "The advert includes anonymous Player1–Player6 current and peak ranks.",
                )
                embed.set_footer(text=marker)
                if recovered:
                    await recovered.edit(embed=embed, view=view)
                    message = recovered
                else:
                    message = await dm.send(
                        embed=embed, view=view, allowed_mentions=discord.AllowedMentions.none()
                    )
                self.store.update_host_notice(self.channel_id, slot.start, generation, "pending", message.id)

    async def decide_notice(self, interaction, start, generation, send):
        guild = self.bot.get_guild(config.GUILD_ID)
        if not guild or interaction.user.id != guild.owner_id:
            raise ValueError("Only the server owner can answer this scrim approval.")
        async with self.notice_lock:
            notice = self.store.host_notices(self.channel_id).get(start)
            if not notice or notice["generation"] != generation or notice["status"] != "pending":
                raise ValueError("This request was already answered or is no longer current.")
            if not send:
                self.store.update_host_notice(self.channel_id, start, generation, "declined")
                return
            slot = next((s for s in self.snapshot() if s.start == start and s.ready), None)
            if not slot:
                self.store.update_host_notice(self.channel_id, start, generation, "expired")
                raise ValueError(
                    "This time no longer has six confirmed players forming 2–2–2. Nothing was sent."
                )
            settings = self.store.host_settings(self.channel_id)
            token = self.preview_token(slot, settings)
            ranks = await self.advert_ranks(slot)
            # Reuse the validated queue path, supplying the owner's guild context for a DM interaction.
            request = SimpleNamespace(user=interaction.user, guild=guild, guild_id=config.GUILD_ID)
            await self.publish(request, start, token, ranks)
            self.store.update_host_notice(self.channel_id, start, generation, "accepted")

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
            description="Choose a day, then a time you can attend.\n"
            "Sessions appear here after the first player confirms.\n"
            f"{settings['duration'] // 60}-minute sessions · 2 Tank / 2 DPS / 2 Support.",
        )
        players = {p["member_id"]: p for p in self.players()}
        confirmed_slots = sorted(
            (s for s in slots if s.confirmed), key=lambda s: (-len(s.confirmed), s.start)
        )
        confirmed_slots = confirmed_slots[:3]
        for slot in confirmed_slots:
            names = [
                f"{role}: "
                + ", ".join(
                    f"<@{mid}>"
                    for mid in sorted(slot.confirmed)
                    if mid in players and players[mid]["answers"].get("preferred_role_1") == role
                )
                for role in ("Tank", "DPS", "Support")
                if any(
                    mid in players and players[mid]["answers"].get("preferred_role_1") == role
                    for mid in slot.confirmed
                )
            ]
            value = (
                f"<t:{slot.start}:F> – <t:{slot.end}:t>\n"
                f"**{len(slot.confirmed)} confirmed**\n"
                + "\n".join(names)
                + f"\n**Available players** ({len(slot.available - slot.confirmed)})\n"
                + (
                    " ".join(f"<@{mid}>" for mid in sorted(slot.available - slot.confirmed))
                    or "None remaining."
                )
            )
            if len(embed.fields) >= 18 or len(embed) + len(value) > 5500:
                break
            embed.add_field(
                name="Ready to host" if slot.ready else "Players confirmed", value=value[:1024], inline=False
            )
        adverts = self.store.host_adverts(self.channel_id)
        for start, advert in sorted(adverts.items()):
            if start <= time.time() or start not in {s.start for s in confirmed_slots}:
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
            index = next(i for i, s in enumerate(confirmed_slots) if s.start == start)
            if index < len(embed.fields):
                field = embed.fields[index]
                embed.set_field_at(
                    index,
                    name=field.name,
                    value=(field.value + f"\nAdvert: {state}{link}")[:1024],
                    inline=False,
                )
        embed.set_footer(text=MARKER)
        return embed

    def board_embeds(self, slots):
        summary = self.board_embed(slots)
        fields = list(summary.fields)
        summary.clear_fields()
        embeds = [summary]
        ranked = sorted((s for s in slots if s.confirmed), key=lambda s: (-len(s.confirmed), s.start))[:3]
        for place, field, slot in zip(("First place", "Second place", "Third place"), fields, ranked):
            embed = discord.Embed(title=f"<t:{slot.start}:F>", color=config.COLOR, description=field.value)
            embed.set_footer(text=f"{place} · {field.name}")
            embeds.append(embed)
        return embeds

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
            embeds = self.board_embeds(slots)
            fingerprint = json.dumps([embed.to_dict() for embed in embeds], sort_keys=True)
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
                    embeds=embeds, view=HostingBoard(self), allowed_mentions=discord.AllowedMentions.none()
                )
            else:
                message = await channel.send(
                    embeds=embeds, view=HostingBoard(self), allowed_mentions=discord.AllowedMentions.none()
                )
            self.store.configure_host(self.channel_id, message_id=message.id)
            self.fingerprint = fingerprint

    async def change_votes(self, member_id, starts, add, expected_duration=None, *, withdraw_starts=()):
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
            for start in withdraw_starts:
                self.store.set_host_vote(self.channel_id, start, member_id, False)
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
            if not settings["destination"]:
                raise ValueError("No advert destination is configured in the collector settings.")
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
        )
