"""Role-safe hosting suggestions, commitments, and explicit publication requests."""

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import discord

from rionnag import config
from rionnag.scrims.scrims import Player, make_team, normalized_roles
from rionnag.services.scrim_availability import covers_interval, same_scrim_day

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


def generate_slots(players, votes, now, duration=7200, days=14, overrides=None):
    """Only show full-interval, best/secondary-only 2–2–2 compositions."""
    players = list({p["member_id"]: p for p in players}.values())
    first = (int(now) // 1800 + 1) * 1800
    result = []
    overrides = overrides or {}
    for start in range(first, int(now) + days * 86400, 1800):
        available = [p for p in players if covers_interval(p["answers"], start, start + duration)
                     or p["member_id"] in overrides.get(start, set()) & votes.get(start, set())]
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
        self.invite_lock = asyncio.Lock()
        self.notification_lock = asyncio.Lock()

    def register_approvals(self):
        from rionnag.ui.scrim_hosting import HostApproval, OfficialInvite, ScrimInvite, SessionCard

        for invite in self.store.host_invites(self.channel_id):
            if invite["status"] == "pending" and invite["message_id"]:
                self.bot.add_view(ScrimInvite(self, invite["member_id"], invite["start"], invite["duration"]),
                                  message_id=invite["message_id"])
        official = self.store.official_host(self.channel_id)
        if official is not None:
            with self.store.connection() as db:
                rows = db.execute("SELECT * FROM scrim_host_notifications WHERE channel_id=? AND start=? "
                                  "AND kind='invite' AND status='sent' AND message_id IS NOT NULL",
                                  (self.channel_id, official)).fetchall()
            for row in rows:
                self.bot.add_view(OfficialInvite(self, row["member_id"], official),
                                  message_id=row["message_id"])

        for position, message_id in self.store.host_cards(self.channel_id).items():
            self.bot.add_view(SessionCard(self, position), message_id=message_id)

        for start, notice in self.store.host_notices(self.channel_id).items():
            if notice["status"] in {"pending", "accepted"} and notice["message_id"]:
                self.bot.add_view(
                    HostApproval(self, start, notice["generation"]), message_id=notice["message_id"]
                )

    def confirmed_roles(self, slot):
        players = {p["member_id"]: p for p in self.players()}
        return "\n".join(
            f"{role}: " + ", ".join(f"<@{mid}>" for mid in sorted(slot.confirmed)
                                    if mid in players
                                    and players[mid]["answers"].get("preferred_role_1") == role)
            for role in ("Tank", "DPS", "Support")
        )

    def owner_panel(self, slot, generation):
        booked = slot.start in self.store.host_bookings(self.channel_id)
        embed = discord.Embed(
            title="Six players confirmed for a scrim", color=config.COLOR,
            description=f"<t:{slot.start}:F> – <t:{slot.end}:t>\n"
            f"**{len(slot.confirmed)} players confirmed**\n{self.confirmed_roles(slot)}\n\n"
            + ("**Officially confirmed**\n" if booked else "")
            + "Enter ranks to send an advert, bump its existing post, or officially confirm the scrim.",
        )
        advert = self.store.host_adverts(self.channel_id).get(slot.start)
        if advert and advert["status"] == "sent" and advert["message_id"]:
            embed.add_field(
                name="Advert sent",
                value=f"[View message](https://discord.com/channels/@me/{advert['destination']}/"
                      f"{advert['message_id']})",
                inline=False,
            )
        embed.set_footer(text=f"Scrim host approval · {slot.start} · {generation}")
        return embed

    async def refresh_owner_panels(self):
        from rionnag.ui.scrim_hosting import HostApproval

        guild = self.bot.get_guild(config.GUILD_ID)
        if not guild:
            return
        notices = self.store.host_notices(self.channel_id)
        slots = {s.start: s for s in self.snapshot()}
        if not any(n["message_id"] and n["status"] in {"pending", "accepted"} for n in notices.values()):
            return
        owner = guild.owner or await self.bot.fetch_user(guild.owner_id)
        dm = owner.dm_channel or await owner.create_dm()
        for start, notice in notices.items():
            if (start not in slots or not notice["message_id"]
                    or notice["status"] not in {"pending", "accepted"}):
                continue
            try:
                message = await dm.fetch_message(notice["message_id"])
                await message.edit(embed=self.owner_panel(slots[start], notice["generation"]),
                                   view=HostApproval(self, start, notice["generation"]))
            except discord.HTTPException:
                log.warning("Could not refresh owner scrim controls; will retry")

    async def manage_session(self, interaction, start, generation, bump):
        guild = self.bot.get_guild(config.GUILD_ID)
        if not guild or interaction.user.id != guild.owner_id:
            raise ValueError("Only the server owner can manage this scrim.")
        async with self.notice_lock:
            notice = self.store.host_notices(self.channel_id).get(start)
            if not notice or notice["generation"] != generation or notice["status"] == "expired":
                raise ValueError("This scrim request is no longer current.")
            slot = next((s for s in self.snapshot() if s.start == start and s.ready), None)
            if not slot:
                raise ValueError("This session no longer has a confirmed 2–2–2 team.")
            if self.blocked_by_official(start):
                raise ValueError("Another scrim is already official on that day.")
            if bump:
                if not self.store.queue_host_bump(self.channel_id, start):
                    raise ValueError("No delivered advert, a bump is pending, or this scrim is booked.")
            else:
                self.store.book_host(self.channel_id, start, slot.end - slot.start, interaction.user.id)
                self.store.update_host_notice(
                    self.channel_id, start, generation, "accepted", notice["message_id"]
                )
        await self.sync()
        if not bump:
            cog = self.bot.get_cog("Scrims") if hasattr(self.bot, "get_cog") else None
            if cog and cog.publisher:
                try:
                    await cog.publisher.sync()
                except Exception:
                    log.exception("Official booking finder refresh failed; minute loop will retry")

    async def answer_official_invite(self, member_id, start, accept):
        if self.store.official_host(self.channel_id) != start:
            raise ValueError("This is no longer the official scrim.")
        settings = self.store.host_settings(self.channel_id)
        if accept:
            await self.direct_join(member_id, start, settings["duration"])
        else:
            await self.change_votes(member_id, [start], False)
        self.store.complete_host_notification(self.channel_id, start, member_id, "invite",
                                              "accepted" if accept else "declined")

    async def notify_official(self):
        from rionnag.ui.scrim_hosting import OfficialInvite

        async with self.notification_lock:
            start = self.store.official_host(self.channel_id)
            if start is None or start <= time.time():
                return
            booking = self.store.host_bookings(self.channel_id).get(start)
            if not booking:
                return
            duration = booking["duration"]
            votes = self.store.host_votes(self.channel_id).get(start, set())
            players = {p["member_id"]: p for p in self.players()}
            for mid, player in sorted(players.items(), key=lambda item: (item[0] not in votes, item[0])):
                available = covers_interval(player["answers"], start, start + duration)
                if mid not in votes and not available:
                    continue
                # A member may join or withdraw after booking; reminders use current votes.
                kinds = ["confirmed" if mid in votes else "invite"]
                if mid in votes:
                    now = time.time()
                    zone = ZoneInfo("America/New_York")
                    if datetime.fromtimestamp(now, zone).date() == datetime.fromtimestamp(start, zone).date():
                        kinds.append("day")
                    if start - 1800 <= now < start:
                        kinds.append("thirty")
                for kind in kinds:
                    record = self.store.host_notification(self.channel_id, start, mid, kind)
                    if record["status"] != "pending":
                        continue
                    marker = f"Official scrim notice · {start} · {mid} · {kind}"
                    try:
                        user = self.bot.get_user(mid) or await self.bot.fetch_user(mid)
                        dm = user.dm_channel or await user.create_dm()
                        recovered = None
                        async for message in dm.history(limit=100):
                            if message.author.id == self.bot.user.id and any(
                                e.footer and e.footer.text == marker for e in message.embeds
                            ):
                                recovered = message
                                break
                        if recovered is None:
                            descriptions = {
                                "confirmed": f"The scrim at <t:{start}:F> is officially confirmed.",
                                "invite": f"The scrim at <t:{start}:F> is officially confirmed. "
                                          "Can you make it? Yes joins; No declines.",
                                "day": f"You have a scrim today at <t:{start}:F>. Please be there on time.",
                                "thirty": f"Your scrim starts in 30 minutes at <t:{start}:F>. "
                                          "Please get ready and join the scrim voice channel.",
                            }
                            embed = discord.Embed(title="Official Rionnag scrim",
                                                  description=descriptions[kind], color=config.COLOR)
                            embed.set_footer(text=marker)
                            recovered = await dm.send(embed=embed,
                                view=OfficialInvite(self, mid, start) if kind == "invite" else None,
                                allowed_mentions=discord.AllowedMentions.none())
                        self.store.complete_host_notification(self.channel_id, start, mid, kind,
                                                              "sent", recovered.id)
                    except discord.Forbidden:
                        self.store.complete_host_notification(self.channel_id, start, mid, kind,
                                                              "undeliverable")
                    except discord.HTTPException:
                        log.warning("Official scrim DM failed; will retry")

    async def notify_ready(self):
        from rionnag.ui.scrim_hosting import HostApproval

        async with self.notice_lock:
            guild = self.bot.get_guild(config.GUILD_ID)
            if guild is None:
                return
            booked = self.store.host_bookings(self.channel_id)
            slots = {s.start: s for s in self.snapshot() if s.ready and s.start not in booked}
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
                embed = self.owner_panel(slot, generation)
                if recovered:
                    await recovered.edit(embed=embed, view=view)
                    message = recovered
                else:
                    message = await dm.send(
                        embed=embed, view=view, allowed_mentions=discord.AllowedMentions.none()
                    )
                self.store.update_host_notice(self.channel_id, slot.start, generation, "pending", message.id)

    async def invite_available(self):
        from rionnag.ui.scrim_hosting import ScrimInvite

        async with self.invite_lock:
            booked = self.store.host_bookings(self.channel_id)
            slots = [s for s in self.snapshot() if len(s.confirmed) >= 3 and s.start not in booked]
            existing = {(i["start"], i["member_id"], i["duration"]): i
                        for i in self.store.host_invites(self.channel_id)}
            for slot in slots:
                duration = slot.end - slot.start
                for mid in sorted(slot.available - slot.confirmed):
                    previous = existing.get((slot.start, mid, duration))
                    if previous and (previous["status"] != "pending" or previous["message_id"]):
                        continue
                    self.store.reserve_host_invite(self.channel_id, slot.start, mid, duration)
                    marker = f"Scrim invitation · {slot.start} · {mid} · {duration}"
                    try:
                        user = self.bot.get_user(mid) or await self.bot.fetch_user(mid)
                        dm = user.dm_channel or await user.create_dm()
                        recovered = None
                        async for message in dm.history(limit=100):
                            if message.author.id == self.bot.user.id and any(
                                e.footer.text == marker for e in message.embeds
                            ):
                                recovered = message
                                break
                        embed = discord.Embed(
                            title="Can you make it for scrims?", color=config.COLOR,
                            description=f"<t:{slot.start}:F> – <t:{slot.end}:t>\n\n"
                            f"**{len(slot.confirmed)} players confirmed.**\n"
                            "You're available for this session.\n"
                            "Can you join? Yes confirms your place; No declines this invitation.",
                        )
                        embed.set_footer(text=marker)
                        view = ScrimInvite(self, mid, slot.start, duration)
                        message = (await recovered.edit(embed=embed, view=view) if recovered else
                                   await dm.send(embed=embed, view=view,
                                                 allowed_mentions=discord.AllowedMentions.none()))
                        self.store.update_host_invite(
                            self.channel_id, slot.start, mid, duration, "pending", message.id
                        )
                    except discord.Forbidden:
                        self.store.update_host_invite(
                            self.channel_id, slot.start, mid, duration, "undeliverable"
                        )
                    except discord.HTTPException:
                        log.warning("Scrim invitation delivery failed; will recover and retry")

    async def answer_invite(self, member_id, start, duration, accept):
        async with self.invite_lock:
            invite = next((i for i in self.store.host_invites(self.channel_id)
                           if (i["start"], i["member_id"], i["duration"])
                           == (start, member_id, duration)), None)
            if not invite or invite["status"] != "pending":
                raise ValueError("This invitation has already been answered or is no longer active.")
            if accept:
                if not await self.direct_join(member_id, start, duration):
                    raise ValueError("Your schedule changed. Use Join scrim on the board to review this.")
            self.store.update_host_invite(
                self.channel_id, start, member_id, duration, "accepted" if accept else "declined"
            )

    async def decide_notice(self, interaction, start, generation, send,
                            rank_range="Grandmaster - Celestial"):
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
            # Reuse the validated queue path, supplying the owner's guild context for a DM interaction.
            request = SimpleNamespace(user=interaction.user, guild=guild, guild_id=config.GUILD_ID)
            await self.publish(request, start, token, rank_range)
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
        slots = generate_slots(
            self.players(),
            self.store.host_votes(self.channel_id),
            time.time() if now is None else now,
            settings["duration"],
            overrides=self.store.host_overrides(self.channel_id, settings["duration"]),
        )
        official = self.store.official_host(self.channel_id)
        return [slot for slot in slots if slot.start == official or not same_scrim_day(slot.start, official)]

    def blocked_by_official(self, start):
        official = self.store.official_host(self.channel_id)
        return start != official and same_scrim_day(start, official)

    def day_closed_for_choices(self, start):
        return same_scrim_day(start, self.store.official_host(self.channel_id))

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
            if slot.start in self.store.host_bookings(self.channel_id):
                value = "**Officially confirmed**\n" + value
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
            bumps = [b for b in self.store.host_bumps(self.channel_id) if b["start"] == start]
            if bumps:
                latest = max(bumps, key=lambda b: b["generation"])
                state += f" · Bump {latest['status']}"
                if latest["error"]:
                    state += f" · {latest['error']}"
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
        embed.set_footer(text="Use /edit_form to view more days")
        return embed

    def board_embeds(self, slots):
        summary = self.board_embed(slots)
        fields = list(summary.fields)
        summary.clear_fields()
        official = self.store.official_host(self.channel_id)
        embeds = [summary]
        status = discord.Embed(title="Scrim status", color=config.COLOR)
        booking = self.store.host_bookings(self.channel_id).get(official)
        if official is not None and official > time.time() and booking:
            status.add_field(
                name="Official upcoming scrim",
                value=f"<t:{official}:F> – <t:{official + booking['duration']}:t>\n"
                      "**Status: Officialized**",
                inline=False,
            )
        adverts = self.store.host_adverts(self.channel_id)
        notices = self.store.host_notices(self.channel_id)
        ready = sorted((s for s in slots if len(s.confirmed) >= 6 and s.start != official),
                       key=lambda s: (-len(s.confirmed), s.start))
        for slot in ready[:5 - len(status.fields)]:
            advert = adverts.get(slot.start)
            notice = notices.get(slot.start)
            if advert and advert["status"] == "sent" and advert["message_id"]:
                state = "Advert sent"
            elif advert and advert["status"] in {"queued", "sending"}:
                state = "Advert queued"
            elif advert and advert["status"] == "uncertain":
                state = "Advert delivery uncertain"
            elif advert and advert["status"] == "failed":
                state = "Advert not sent (delivery failed)"
            elif notice and notice["message_id"] and notice["status"] in {"pending", "accepted"}:
                state = "Owner message sent · Advert not sent"
            else:
                state = "Advert not sent"
            if not slot.ready:
                state += " · Needs a valid 2–2–2 lineup"
            status.add_field(
                name=f"{len(slot.confirmed)} players confirmed for a scrim",
                value=f"<t:{slot.start}:F> – <t:{slot.end}:t>\n**Status: {state}**",
                inline=False,
            )
        if status.fields:
            embeds.insert(0, status)
        ranked = sorted((s for s in slots if s.confirmed), key=lambda s: (-len(s.confirmed), s.start))[:3]
        for field, slot in zip(fields, ranked):
            embed = discord.Embed(title=f"<t:{slot.start}:F>", color=config.COLOR, description=field.value)
            embeds.append(embed)
        return embeds

    async def sync(self):
        from rionnag.ui.scrim_hosting import HostingBoard, SessionCard

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
            board_count = 2 if embeds[0].title == "Scrim status" else 1
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
                        e.footer.text == MARKER or (
                            e.footer.text == "Use /edit_form to view more days" and e.title == "HOST SCRIMS"
                        ) for e in candidate.embeds
                    ):
                        message = candidate
                        break
            if message:
                await message.edit(
                    embeds=embeds[:board_count], view=HostingBoard(self),
                    allowed_mentions=discord.AllowedMentions.none()
                )
            else:
                message = await channel.send(
                    embeds=embeds[:board_count], view=HostingBoard(self),
                    allowed_mentions=discord.AllowedMentions.none()
                )
            self.store.configure_host(self.channel_id, message_id=message.id)
            cards = self.store.host_cards(self.channel_id)
            for position, mid in cards.items():
                if position >= 3:
                    try:
                        obsolete = await channel.fetch_message(mid)
                        await obsolete.delete()
                    except discord.NotFound:
                        pass
                    self.store.remove_host_card(self.channel_id, position)
            for position in range(3):
                marker = f"Top scrim option {position + 1}"
                card = None
                if position in cards:
                    try:
                        card = await channel.fetch_message(cards[position])
                    except discord.NotFound:
                        pass
                if card is None:
                    async for candidate in channel.history(limit=100):
                        if candidate.author.id == self.bot.user.id and candidate.content in {
                            marker, f"**Scrim option {position + 1}**"
                        }:
                            card = candidate
                            break
                if position + board_count < len(embeds):
                    embed = embeds[position + board_count]
                else:
                    embed = discord.Embed(
                        title=f"Scrim #{position + 1}",
                        description="No confirmed session in this position yet.", color=config.COLOR,
                    )
                view = SessionCard(self, position, disabled=position + board_count >= len(embeds))
                kwargs = dict(content=marker, embed=embed, view=view,
                              allowed_mentions=discord.AllowedMentions.none())
                card = await card.edit(**kwargs) if card else await channel.send(**kwargs)
                self.store.save_host_card(self.channel_id, position, card.id)
            self.fingerprint = fingerprint

    async def direct_join(self, member_id, start, duration, override=False):
        async with self.lock:
            if self.blocked_by_official(start):
                raise ValueError("Another scrim is already official on that day.")
            settings = self.store.host_settings(self.channel_id)
            players = {p["member_id"]: p for p in self.players()}
            if member_id not in players:
                raise ValueError("Complete your current Marvel Rivals form and restore your game roles.")
            if duration != settings["duration"] or not any(s.start == start for s in self.snapshot()):
                raise ValueError("This session changed or expired. Reopen the board.")
            if not covers_interval(players[member_id]["answers"], start, start + duration):
                if not override:
                    return False
                self.store.set_host_override(self.channel_id, start, member_id, duration)
            self.store.set_host_vote(self.channel_id, start, member_id, True)
        await self.sync()
        return True

    async def change_votes(self, member_id, starts, add, expected_duration=None, *, withdraw_starts=()):
        async with self.lock:
            if add and any(self.day_closed_for_choices(start) for start in starts):
                raise ValueError(
                    "A scrim is already official on that day. Use its Join button or invitation."
                )
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

    async def publish(self, interaction, start, expected, rank_range="Grandmaster - Celestial"):
        if not self.manager(interaction):
            raise ValueError("Only the owner or Marvel Rivals Managers can publish adverts.")
        async with self.lock:
            if self.blocked_by_official(start):
                raise ValueError("Another scrim is already official on that day.")
            if start in self.store.host_bookings(self.channel_id):
                raise ValueError("This scrim is officially confirmed; no new advert will be sent.")
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
                    self.channel_id, start, settings, slot.lineup, interaction.user.id, rank_range
                )
            elif not self.store.queue_host_advert(
                self.channel_id, start, settings, slot.lineup, interaction.user.id, rank_range
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
