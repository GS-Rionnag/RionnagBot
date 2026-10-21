"""Match today's collected offers and maintain one interactive finder board."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import discord

from rionnag import config
from rionnag.scrims.scrim_offer_rules import timestamp
from rionnag.services.scrim_availability import covers_interval, same_scrim_day
from rionnag.services.scrim_hosting import role_team
from rionnag.services.scrim_search import rank_matches
from rionnag.ui.scrim_opportunities import (
    BOARD_MARKER,
    FinderInvite,
    FinderOwnerApproval,
    ScheduleOverride,
)

log = logging.getLogger(__name__)
SERVER_ZONE = ZoneInfo("America/New_York")


def offer_interval(offer):
    start = timestamp(offer.get("Start_Time_timestamp"))
    end_value = offer.get("End_Time_timestamp")
    end = timestamp(end_value) if end_value else (start + 3600 if start is not None else None)
    if start is None or end is None or end - start < 3600:
        return None
    return start, end


def match_offer(offer, players, now):
    interval = offer_interval(offer)
    if interval is None or interval[1] <= now:
        return []
    return sorted({p["member_id"] for p in players
                   if covers_interval(p["answers"], *interval)})


def source_order(key):
    message_id, position = key.split(":")
    return int(message_id), int(position)


def source_author(offer, key):
    return str(offer.get("authorID") or f"source:{key.split(':')[0]}")


def select_offers(offers):
    groups = {}
    for key, offer in offers.items():
        start = timestamp(offer.get("Start_Time_timestamp"))
        if start is not None:
            authors = groups.setdefault(start, {})
            author = source_author(offer, key)
            if author not in authors or source_order(key) > source_order(authors[author]):
                authors[author] = key
    return {start: (max(authors.values(), key=source_order), len(authors))
            for start, authors in groups.items()}


def post_start(key, post, offers):
    if post["start_time"] is not None:
        return post["start_time"]
    offer = offers.get(post["source_key"] or key, {})
    return timestamp(offer.get("Start_Time_timestamp"))


def server_day(when):
    return datetime.fromtimestamp(when, SERVER_ZONE).date().isoformat()


class OpportunityPublisher:
    def __init__(self, bot, store, feed, forms, guild_id, channel_id):
        self.bot, self.store, self.feed, self.forms = bot, store, feed, forms
        self.guild_id, self.channel_id = guild_id, channel_id
        self.lock = asyncio.Lock()
        self._legacy_dm_scan_done = False
        self._static_board_refreshed = False

    def register_views(self):
        # The public opportunities channel is a read-only list, not a signup or ping surface.
        return None

    async def cleanup_completed_scrim_dms(self, now=None):
        """Delete completed bot scrim messages, including old messages predating stored IDs."""
        now = time.time() if now is None else now
        guild = self.bot.get_guild(self.guild_id)
        if guild is None:
            return
        channels = {}
        seen = set()
        for row in self.store.scrim_dm_messages(guild.owner_id):
            message_id = row["message_id"]
            obsolete = row.get("status") in {"accepted", "declined", "expired", "undeliverable"}
            deadline = row["start"] if row.get("kind") == "thirty" else row["start"] + row["duration"]
            if (not obsolete and deadline > now) or message_id in seen:
                continue
            seen.add(message_id)
            try:
                dm = channels.get(row["member_id"])
                if dm is None:
                    get_user = getattr(self.bot, "get_user", lambda _: None)
                    user = get_user(row["member_id"])
                    if user is None:
                        fetch_user = getattr(self.bot, "fetch_user", None)
                        if fetch_user is None:
                            continue
                        user = await fetch_user(row["member_id"])
                    dm = getattr(user, "dm_channel", None)
                    if dm is None:
                        create_dm = getattr(user, "create_dm", None)
                        if create_dm is None:
                            continue
                        dm = await create_dm()
                    channels[row["member_id"]] = dm
                message = await dm.fetch_message(message_id)
                if message.author.id == self.bot.user.id:
                    await message.delete()
                self.store.forget_scrim_dm_message(message_id)
            except discord.NotFound:
                self.store.forget_scrim_dm_message(message_id)
                continue
            except discord.Forbidden:
                log.info("Could not delete a completed scrim DM")
            except discord.HTTPException:
                log.warning("Could not delete a completed scrim DM; will retry")
        if not self._legacy_dm_scan_done:
            self._legacy_dm_scan_done = await self._scan_legacy_scrim_dms(guild, now)

    async def _scan_legacy_scrim_dms(self, guild, now):
        """One-time history sweep for bot-authored scrim DMs without persisted message IDs."""
        bot_id = self.bot.user.id
        members = {getattr(member, "id", index): member
                   for index, member in enumerate(getattr(guild, "members", ()))}
        complete = True
        if guild.owner_id not in members:
            owner = getattr(guild, "owner", None)
            fetch_user = getattr(self.bot, "fetch_user", None)
            if owner is None and fetch_user is not None:
                owner = await fetch_user(guild.owner_id)
            if owner is not None:
                members[guild.owner_id] = owner
            else:
                complete = False
        current_host_notices = self.store.host_notices(config.SCRIM_HOST_CHANNEL_ID)
        for member in members.values():
            if getattr(member, "bot", False):
                continue
            try:
                dm = getattr(member, "dm_channel", None)
                if dm is None:
                    create_dm = getattr(member, "create_dm", None)
                    if create_dm is None:
                        complete = False
                        continue
                    dm = await create_dm()
                history = getattr(dm, "history", None)
                if history is None:
                    complete = False
                    continue
                async for message in history(limit=None):
                    if message.author.id != bot_id:
                        continue
                    text = " ".join(filter(None, [
                        getattr(message, "content", None),
                        *(part for embed in getattr(message, "embeds", ()) for part in (
                            getattr(embed, "title", None), getattr(embed, "description", None),
                            (getattr(embed, "footer", None).text
                             if getattr(embed, "footer", None) else None),
                        )),
                    ]))
                    lowered = text.casefold()
                    transient_scrim_reply = any(phrase in lowered for phrase in (
                        "bump queued. the old advert will be deleted and reposted.",
                        "this session no longer has a confirmed 2–2–2 team.",
                        "advert queued. delivery status will appear on the scrim board.",
                        "advert queued with your rank range.",
                        "declined. no advert will be sent for this request.",
                        "scrim officially confirmed. the main board is updated.",
                    ))
                    if transient_scrim_reply:
                        await message.delete()
                        self.store.forget_scrim_dm_message(message.id)
                        continue
                    embeds = getattr(message, "embeds", ())
                    footer = " ".join(embed.footer.text for embed in embeds
                                      if embed.footer and embed.footer.text)
                    host_approval = re.search(
                        r"Scrim host approval\s*·\s*(\d+)\s*·\s*(\d+)", footer
                    )
                    if host_approval:
                        notice = current_host_notices.get(int(host_approval[1]))
                        if (not notice or notice["generation"] != int(host_approval[2])
                                or notice["status"] not in {"pending", "accepted"}):
                            await message.delete()
                            self.store.forget_scrim_dm_message(message.id)
                            continue
                    if "scrim" not in text.casefold():
                        continue
                    start, duration = self._scrim_dm_window(footer)
                    if start is None:
                        match = re.search(r"<t:(\d+)(?::[tTdDfFR])?>", text)
                        if match:
                            start, duration = int(match[1]), 7200
                    if start is None or start + duration > now:
                        continue
                    await message.delete()
                    self.store.forget_scrim_dm_message(message.id)
            except discord.NotFound:
                continue
            except discord.Forbidden:
                log.info("Could not scan a member's scrim DM history")
            except discord.HTTPException:
                log.warning("Could not scan a member's scrim DM history")
                complete = False
        return complete

    @staticmethod
    def _scrim_dm_window(footer):
        finder = re.search(r"Scrim finder notice\s*•\s*\d+:(\d+):\d+:[^:]+:(\d+)", footer)
        if finder:
            return int(finder[1]), int(finder[2])
        invite = re.search(r"Scrim invitation\s*·\s*(\d+)\s*·\s*\d+\s*·\s*(\d+)", footer)
        if invite:
            return int(invite[1]), int(invite[2])
        official = re.search(r"Official scrim notice\s*·\s*(\d+)\s*·\s*\d+\s*·", footer)
        if official:
            return int(official[1]), 7200
        owner = re.search(r"Scrim finder owner\s*•\s*\d+:(\d+):", footer)
        if owner:
            return int(owner[1]), 7200
        host = re.search(r"Scrim host approval\s*·\s*(\d+)\s*·", footer)
        if host:
            return int(host[1]), 7200
        return None, 7200

    def eligible_players(self, guild, form):
        role_ids = {form[k] for k in ("tryout_role", "team_role", "manager_role")}
        return [p for p in self.store.scrim_candidates(self.guild_id, "marvel-rivals", form["version"])
                if (member := guild.get_member(p["member_id"])) is not None and not member.bot
                and any(role.id in role_ids for role in member.roles)]

    def _snapshot(self, now):
        guild = self.bot.get_guild(self.guild_id)
        form = self.forms.get("marvel-rivals")
        if guild is None or form is None:
            return None
        players = self.eligible_players(guild, form)
        offers, revisions = self.feed.opportunity_snapshot()
        official = self.store.official_host(config.SCRIM_HOST_CHANNEL_ID)
        bookings = self.store.finder_bookings(self.channel_id)
        ranks = self.store.scrim_rank_filter(self.channel_id)
        scheduled = {key: match_offer(offer, players, now) for key, offer in offers.items()}
        matched = {}
        for key, offer in offers.items():
            interval = offer_interval(offer)
            declined = self.store.finder_declined(self.channel_id, *interval) if interval else set()
            matched[key] = [mid for mid in scheduled[key] if mid not in declined]
        qualified = {key: offer for key, offer in offers.items()
                     if rank_matches(offer, ranks)
                     and not same_scrim_day(timestamp(offer.get("Start_Time_timestamp")), official)
                     and not any(same_scrim_day(timestamp(offer.get("Start_Time_timestamp")), start)
                                 and timestamp(offer.get("Start_Time_timestamp")) != start
                                 for start in bookings)}
        selected = {start: value for start, value in select_offers(qualified).items()
                    if start > now}
        return guild, players, offers, revisions, matched, selected

    async def current_slots(self):
        snapshot = self._snapshot(time.time())
        if not snapshot:
            return []
        selected = snapshot[-1]
        posts = self.store.opportunity_posts(self.channel_id)
        result = []
        for start, (source_key, _) in sorted(selected.items()):
            if start in self.store.finder_bookings(self.channel_id):
                continue
            key = next((key for key, post in posts.items()
                        if post["status"] == "active" and post["start_time"] == start), source_key)
            result.append((key, datetime.fromtimestamp(start, SERVER_ZONE)
                           .strftime("%I:%M %p").lstrip("0")))
        return result

    async def slot_detail(self, key):
        now = time.time()
        snapshot = self._snapshot(now)
        post = self.store.opportunity_posts(self.channel_id).get(key)
        if not snapshot or not post or post["status"] != "active":
            return None
        _, _, offers, _, _, selected = snapshot
        start = post["start_time"]
        if start in self.store.finder_bookings(self.channel_id):
            return None
        current = selected.get(start)
        if not current or current[0] != post["source_key"]:
            return None
        offer = offers.get(post["source_key"])
        interval = offer_interval(offer) if offer else None
        return (offer, *interval) if interval else None

    async def sync(self, now=None):
        async with self.lock:
            await self.cleanup_completed_scrim_dms(now)
            await self._sync(now)

    async def _sync(self, now=None):
        now = time.time() if now is None else now
        snapshot = self._snapshot(now)
        if not snapshot:
            return
        guild, players, offers, revisions, matched, selected = snapshot
        channel = guild.get_channel(self.channel_id)
        if channel is None:
            log.warning("Scrim opportunity channel is unavailable")
            return
        posts = self.store.opportunity_posts(self.channel_id)
        board = self.store.finder_board(self.channel_id)
        board_id = board["message_id"] if board else None
        anchors = {}
        for key, post in sorted(posts.items(), key=lambda item: (
                item[1]["status"] == "inactive", item[1]["message_id"] is None, item[0])):
            start = post_start(key, post, offers)
            if start in selected:
                anchors.setdefault(start, key)

        # Old per-time posts and the former vote summary are removed after a
        # board exists. A failed deletion is retried on the next refresh.
        for key, post in posts.items():
            if key in anchors.values():
                continue
            if post["message_id"] and post["message_id"] != board_id:
                try:
                    message = await channel.fetch_message(post["message_id"])
                    await message.delete()
                except discord.NotFound:
                    pass
                except discord.HTTPException:
                    log.warning("Could not remove old finder post; will retry")
                    return
            self.store.save_opportunity(self.channel_id, key, None, None, "inactive", reset_votes=True)
            self.store.clear_finder_override(self.channel_id, key)

        embeds = [discord.Embed(
            title="Upcoming scrims", color=config.COLOR,
            description="Collected scrims, ordered by start time. Times display in your Discord time zone."
        )]
        active = []
        detail_limit = 9 if len(selected) <= 9 else 8
        for position, (start, (source_key, _count)) in enumerate(sorted(selected.items())):
            offer = offers[source_key]
            key = anchors.get(start) or (source_key if source_key not in posts else f"time:{start}")
            interval = offer_interval(offer)
            ranks = list(dict.fromkeys(offer.get(name) for name in ("rank_minimum", "rank_maximum")
                                       if offer.get(name)))
            description = (f"Ends <t:{interval[1]}:t>" if interval else "End time not listed.")
            embed = discord.Embed(title=f"<t:{start}:F>", description=description, color=config.COLOR)
            embed.add_field(name="Opponent rank", value=" to ".join(ranks) or "Not specified")
            if offer.get("messageURL"):
                embed.add_field(name="Original post", value=f"[View scrim advert]({offer['messageURL']})")
            if position < detail_limit:
                embeds.append(embed)
            else:
                overflow = embeds[-1]
                if overflow.title != "More scrims":
                    overflow = discord.Embed(title="More scrims", color=config.COLOR)
                    embeds.append(overflow)
                line = f"<t:{start}:F> · {(' to '.join(ranks) or 'Rank not specified')}"
                if offer.get("messageURL"):
                    line += f" · [Advert]({offer['messageURL']})"
                overflow.description = ((overflow.description or "") + "\n" + line)[-4000:]
            active.append((key, start, source_key, revisions[source_key],
                           source_author(offer, source_key), [], offer))
        if len(embeds) == 1:
            embeds[0].description += "\n\nNo upcoming scrims found."
        embeds[0].set_footer(text=BOARD_MARKER)
        fingerprint = hashlib.sha256(json.dumps(
            [embed.to_dict() for embed in embeds], sort_keys=True).encode()).hexdigest()

        message = None
        try:
            if board_id:
                try:
                    message = await channel.fetch_message(board_id)
                except discord.NotFound:
                    pass
            if message is None:
                async for candidate in channel.history(limit=100):
                    if candidate.author.id == self.bot.user.id and any(
                            embed.footer.text == BOARD_MARKER for embed in candidate.embeds):
                        message = candidate
                        break
            if message is None:
                message = await channel.send(embeds=embeds,
                                             allowed_mentions=discord.AllowedMentions.none())
            elif not board or board["fingerprint"] != fingerprint or not self._static_board_refreshed:
                await message.edit(embeds=embeds, view=None,
                                   allowed_mentions=discord.AllowedMentions.none())
            self.store.save_finder_board(self.channel_id, message.id, server_day(now), fingerprint)
            self._static_board_refreshed = True
            for key, start, source_key, revision, author, _, _ in active:
                self.store.save_opportunity(self.channel_id, key, message.id, fingerprint, "active",
                                            start, source_key, revision, author)
            old_ids = {posts[key]["message_id"] for key, *_ in active if key in posts
                       and posts[key]["message_id"] not in (None, message.id)}
            for old_id in old_ids:
                try:
                    old_message = await channel.fetch_message(old_id)
                    await old_message.delete()
                except discord.NotFound:
                    pass
            async for candidate in channel.history(limit=100):
                if (candidate.id != message.id and candidate.author.id == self.bot.user.id
                        and any(embed.footer.text == BOARD_MARKER for embed in candidate.embeds)):
                    await candidate.delete()
        except discord.HTTPException:
            log.warning("Daily scrim board delivery failed; will retry", exc_info=False)
            return

        old_summary = self.store.vote_summary_message(self.channel_id)
        if old_summary and old_summary != message.id:
            try:
                summary = await channel.fetch_message(old_summary)
                await summary.delete()
            except discord.NotFound:
                pass
            except discord.HTTPException:
                log.warning("Could not remove old vote summary; will retry")
            else:
                self.store.save_vote_summary_message(self.channel_id, message.id)

    async def _sync_owner_notices(self, guild, players, active):
        notices = self.store.finder_notices(self.channel_id)
        ready = {}
        for key, start, _, _, _, votes, offer in active:
            if len(votes) == 6:
                lineup, _ = role_team([p for p in players if p["member_id"] in votes])
                if lineup:
                    ready[start] = (key, votes, offer, lineup)
        for start, notice in notices.items():
            current_key = ready[start][0] if start in ready else None
            current_source = next((source for key, s, source, *_ in active
                                   if s == start and key == current_key), None)
            if (notice["status"] == "pending" and
                    (start not in ready or notice["source_key"] != current_source)):
                self.store.save_finder_notice(self.channel_id, start, notice["generation"],
                                              "expired", notice["message_id"], notice["source_key"])
        owner = guild.get_member(guild.owner_id) if hasattr(guild, "owner_id") else None
        if owner is None:
            return
        for start, (key, votes, offer, lineup) in ready.items():
            if start in self.store.finder_bookings(self.channel_id):
                continue
            old = notices.get(start)
            source_key = next(source for k, s, source, *_ in active if s == start and k == key)
            if old and old["status"] in ("pending", "booked") and old["source_key"] == source_key:
                continue
            generation = old["generation"] + 1 if old else 1
            author = str(offer.get("authorID", ""))
            author_text = f"<@{author}>" if author.isdigit() else "the advert author"
            lines = [f"{role}: " + ", ".join(f"<@{mid}>" for mid in votes
                     if lineup.get(mid) == role) for role in ("Tank", "DPS", "Support")]
            embed = discord.Embed(
                title="Six players confirmed for a scrim", color=config.COLOR,
                description=f"<t:{start}:F>\n" + "\n".join(lines)
                + f"\n\nPlease message {author_text} about the advert, then confirm "
                  "the scrim here once agreed.",
            )
            if offer.get("messageURL"):
                embed.add_field(name="Advert", value=f"[View original post]({offer['messageURL']})")
            marker = f"Scrim finder owner • {self.channel_id}:{start}:{generation}:{source_key}"
            embed.set_footer(text=marker)
            try:
                dm = None
                recovered = False
                if hasattr(owner, "create_dm"):
                    channel = await owner.create_dm()
                    async for candidate in channel.history(limit=30):
                        if candidate.author.id == self.bot.user.id and any(
                                e.footer.text == marker for e in candidate.embeds):
                            dm = candidate
                            recovered = True
                            break
                if dm is None:
                    dm = await owner.send(embed=embed, view=FinderOwnerApproval(self, start, generation),
                                          allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException:
                log.warning("Could not send finder readiness DM; will retry")
                continue
            self.store.save_finder_notice(self.channel_id, start, generation, "pending", dm.id,
                                          source_key)
            if recovered:
                self.bot.add_view(FinderOwnerApproval(self, start, generation), message_id=dm.id)

    async def _deliver_player_notice(self, guild, start, member_id, kind, duration, offer):
        notice = self.store.finder_notification(self.channel_id, start, member_id, kind, duration)
        if notice["status"] != "pending":
            return
        member = guild.get_member(member_id)
        if member is None or not hasattr(member, "create_dm"):
            return
        marker = f"Scrim finder notice • {self.channel_id}:{start}:{member_id}:{kind}:{duration}"
        if kind == "interest":
            description = (f"<t:{start}:F> – <t:{start + duration}:t>\n"
                           "Three players have confirmed. You are available for this time. "
                           "Would you like to join?")
            title = "Scrim invitation"
        elif kind == "official_invite":
            description = (f"<t:{start}:F> – <t:{start + duration}:t>\n"
                           "The server owner confirmed this scrim. You are available for this time. "
                           "Would you like to join?")
            title = "Official scrim invitation"
        elif kind == "official":
            description = f"<t:{start}:F> – <t:{start + duration}:t>\nThe server owner confirmed this scrim."
            title = "Your scrim is official"
        elif kind == "day_reminder":
            description = f"<t:{start}:F> – <t:{start + duration}:t>\nYour confirmed scrim is today."
            title = "Scrim today"
        else:
            description = f"<t:{start}:F> – <t:{start + duration}:t>\nYour scrim starts in 30 minutes."
            title = "Scrim starting soon"
        embed = discord.Embed(title=title, description=description, color=config.COLOR)
        if offer.get("messageURL"):
            embed.add_field(name="Opponent advert",
                            value=f"[View original post]({offer['messageURL']})")
        embed.set_footer(text=marker)
        try:
            dm = await member.create_dm()
            existing = None
            recovered = False
            for previous in self.store.finder_notifications(self.channel_id):
                if (previous["start"] == start and previous["member_id"] == member_id
                        and previous["message_id"] and previous["kind"] != kind):
                    try:
                        if hasattr(dm, "fetch_message"):
                            existing = await dm.fetch_message(previous["message_id"])
                    except discord.HTTPException:
                        existing = None
                    if existing is not None:
                        recovered = True
                        break
            async for candidate in dm.history(limit=30):
                if existing is not None:
                    break
                if candidate.author.id == self.bot.user.id and any(
                        e.footer and e.footer.text.startswith(
                            f"Scrim finder notice • {self.channel_id}:{start}:{member_id}:")
                        for e in candidate.embeds):
                    existing = candidate
                    recovered = True
                    break
            view = FinderInvite(self, member_id, start, duration, kind) if kind in (
                "interest", "official_invite") else None
            if existing is None:
                existing = await dm.send(embed=embed, view=view,
                                         allowed_mentions=discord.AllowedMentions.none())
            else:
                await existing.edit(embed=embed, view=view)
            self.store.complete_finder_notification(
                self.channel_id, start, member_id, kind, duration, "sent", existing.id)
            if recovered and kind in ("interest", "official_invite"):
                self.bot.add_view(FinderInvite(self, member_id, start, duration, kind),
                                  message_id=existing.id)
        except discord.HTTPException:
            self.store.complete_finder_notification(
                self.channel_id, start, member_id, kind, duration, "undeliverable")
            log.warning("Could not deliver finder %s DM to %s", kind, member_id)

    async def _sync_player_notifications(self, guild, players, active, now):
        player_ids = {p["member_id"] for p in players}
        bookings = self.store.finder_bookings(self.channel_id)
        for key, start, _, _, _, votes, offer in active:
            interval = offer_interval(offer)
            duration = interval[1] - interval[0]
            available = {p["member_id"] for p in players
                         if covers_interval(p["answers"], *interval)}
            available -= self.store.finder_declined(self.channel_id, *interval)
            if start in bookings:
                for mid in votes:
                    if mid in player_ids:
                        await self._deliver_player_notice(guild, start, mid, "official",
                                                          duration, offer)
                        if server_day(now) == server_day(start):
                            await self._deliver_player_notice(guild, start, mid, "day_reminder",
                                                              duration, offer)
                        if start - 1800 <= now < start:
                            await self._deliver_player_notice(guild, start, mid, "half_hour",
                                                              duration, offer)
                for mid in sorted(available - set(votes)):
                    await self._deliver_player_notice(guild, start, mid, "official_invite",
                                                      duration, offer)
            elif len(votes) >= 3:
                for mid in sorted(available - set(votes)):
                    await self._deliver_player_notice(guild, start, mid, "interest", duration, offer)

    async def answer_invite(self, member_id, start, duration, kind, accept):
        async with self.lock:
            notice = self.store.finder_notification(
                self.channel_id, start, member_id, kind, duration)
            if notice["status"] not in ("sent", "accepted", "declined"):
                raise ValueError("This invitation is no longer available.")
            if notice["status"] in ("accepted", "declined"):
                raise ValueError("You already answered this invitation.")
            if not accept:
                self.store.decline_finder_interval(
                    self.channel_id, start, start + duration, member_id, kind, duration)
                await self._sync()
                return
            snapshot = self._snapshot(time.time())
            if not snapshot or start not in snapshot[-1]:
                raise ValueError("This scrim opportunity is no longer available.")
            _, players, offers, _, _, selected = snapshot
            player = next((p for p in players if p["member_id"] == member_id), None)
            source_key = selected[start][0]
            interval = offer_interval(offers[source_key])
            if member_id in self.store.finder_declined(self.channel_id, *interval):
                raise ValueError("You marked this time unavailable.")
            if (player is None or interval[1] - interval[0] != duration
                    or not covers_interval(player["answers"], *interval)):
                raise ValueError("Your eligibility or availability changed. Use the board to review.")
            key = next((key for key, post in self.store.opportunity_posts(self.channel_id).items()
                        if post["status"] == "active" and post["start_time"] == start), None)
            if key is None:
                raise ValueError("This scrim opportunity is no longer available.")
            if accept:
                try:
                    self.store.vote_opportunity(self.channel_id, key, member_id, True)
                except ValueError as exc:
                    raise ValueError(str(exc)) from exc
            self.store.complete_finder_notification(
                self.channel_id, start, member_id, kind, duration,
                "accepted" if accept else "declined")
            await self._sync()

    async def vote(self, interaction, key, add, override=False):
        await interaction.response.defer(ephemeral=True)
        async with self.lock:
            now = time.time()
            snapshot = self._snapshot(now)
            post = self.store.opportunity_posts(self.channel_id).get(key)
            if not snapshot or not post or post["status"] != "active":
                await interaction.followup.send("This scrim opportunity is no longer available.",
                                                ephemeral=True)
                return
            guild, players, offers, _, matched, selected = snapshot
            ids = {p["member_id"] for p in players}
            start = post["start_time"]
            current = selected.get(start)
            if interaction.guild_id != self.guild_id or interaction.user.id not in ids:
                await interaction.followup.send(
                    "Only eligible Marvel Rivals players with a completed current form can join.",
                    ephemeral=True)
                return
            if not current or current[0] != post["source_key"]:
                await interaction.followup.send("This scrim opportunity changed. Choose it again.",
                                                ephemeral=True)
                return
            offer = offers[post["source_key"]]
            interval = offer_interval(offer)
            duration = interval[1] - interval[0]
            player = next(p for p in players if p["member_id"] == interaction.user.id)
            if add and interaction.user.id in self.store.finder_declined(self.channel_id, *interval):
                await interaction.followup.send(
                    "You declined a scrim invitation during this time, so you are marked unavailable.",
                    ephemeral=True)
                return
            available = covers_interval(player["answers"], *interval)
            if add and not available and not override:
                await interaction.followup.send(
                    f"<t:{start}:F> – <t:{interval[1]}:t>\n"
                    "This time is outside your saved availability. Are you sure you can attend?",
                    view=ScheduleOverride(self, interaction.user.id, key), ephemeral=True)
                return
            if add and override:
                self.store.set_finder_override(self.channel_id, key, interaction.user.id, duration)
            try:
                self.store.vote_opportunity(self.channel_id, key, interaction.user.id, add)
            except ValueError as exc:
                await interaction.followup.send(str(exc), ephemeral=True)
                return
            if not add:
                self.store.clear_finder_override(self.channel_id, key, interaction.user.id)
            await self._sync(now)
        await interaction.followup.send(
            "You are confirmed for this scrim." if add else "You withdrew from this scrim.",
            ephemeral=True)

    async def book(self, interaction, start, generation):
        async with self.lock:
            guild = self.bot.get_guild(self.guild_id)
            if guild is None or interaction.user.id != guild.owner_id:
                raise ValueError("Only the server owner can confirm a scrim.")
            notice = self.store.finder_notices(self.channel_id).get(start)
            if not notice or notice["generation"] != generation or notice["status"] != "pending":
                raise ValueError("This scrim request is no longer current.")
            snapshot = self._snapshot(time.time())
            if not snapshot or start not in snapshot[-1]:
                raise ValueError("This scrim opportunity is no longer available.")
            _, players, offers, _, _, selected = snapshot
            source_key = selected[start][0]
            key, post = next(((key, post) for key, post in
                              self.store.opportunity_posts(self.channel_id).items()
                              if post["status"] == "active" and post["start_time"] == start),
                             (None, None))
            if not post or post["source_key"] != source_key:
                raise ValueError("The advert changed. Review the new six-player notice.")
            if notice["source_key"] != source_key:
                raise ValueError("The advert changed. Review the new six-player notice.")
            votes = self.store.opportunity_votes(self.channel_id, key)
            lineup, _ = role_team([p for p in players if p["member_id"] in votes])
            if len(votes) != 6 or not lineup:
                raise ValueError("A valid confirmed six-player lineup is required.")
            interval = offer_interval(offers[source_key])
            self.store.book_finder(self.channel_id, start, interval[1] - start, interaction.user.id)
            self.store.save_finder_notice(self.channel_id, start, generation, "booked",
                                          notice["message_id"], source_key)
            await self._sync()
