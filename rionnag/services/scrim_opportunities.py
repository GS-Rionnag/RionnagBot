"""Match recurring player schedules and maintain the automatic opportunity log."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import discord

from rionnag.scrims.scrim_offer_rules import timestamp
from rionnag.services.scrim_search import rank_matches
from rionnag.ui.scrim_opportunities import OpportunityVotes, matched_offer_embed

log = logging.getLogger(__name__)
MIN_AVAILABLE_PLAYERS = 6
ZONES = {
    "Eastern Time (ET)": "America/New_York",
    "Central Time (CT)": "America/Chicago",
    "Mountain Time (MT)": "America/Denver",
    "Pacific Time (PT)": "America/Los_Angeles",
}


def offer_interval(offer):
    start = timestamp(offer.get("Start_Time_timestamp"))
    end_value = offer.get("End_Time_timestamp")
    end = timestamp(end_value) if end_value else (start + 3600 if start is not None else None)
    if start is None or end is None or end - start < 3600:
        return None
    return start, end


def local_boundary(wall, zone, is_start):
    """Skip nonexistent clocks; use the conservative side of ambiguous clocks."""
    values = []
    for fold in (0, 1):
        local = wall.replace(tzinfo=zone, fold=fold)
        instant = local.timestamp()
        if datetime.fromtimestamp(instant, zone).replace(tzinfo=None) == wall:
            values.append(instant)
    return (max(values) if is_start else min(values)) if values else None


def covers_interval(answers, start, end):
    zone_name = ZONES.get(answers.get("time_zone"))
    days = answers.get("availability_days")
    if not zone_name or not isinstance(days, dict) or not days:
        return False
    zone = ZoneInfo(zone_name)
    first = datetime.fromtimestamp(start, zone).date() - timedelta(days=1)
    last = datetime.fromtimestamp(end, zone).date()
    intervals = []
    date = first
    while date <= last:
        window = days.get(date.strftime("%A"))
        if isinstance(window, dict):
            a, b = window.get("start"), window.get("end")
            if type(a) is int and type(b) is int and 0 <= a < 24 and 0 <= b <= 24 and a != b:
                midnight = datetime.combine(date, datetime.min.time())
                left = local_boundary(midnight + timedelta(hours=a), zone, True)
                right = local_boundary(midnight + timedelta(hours=b + (24 if b <= a else 0)), zone, False)
                if left is not None and right is not None and right > left:
                    intervals.append((left, right))
        date += timedelta(days=1)
    covered = start
    for left, right in sorted(intervals):
        if left > covered:
            break
        if right >= covered:
            covered = right
        if covered >= end:
            return True
    return False


def match_offer(offer, players, now):
    interval = offer_interval(offer)
    if interval is None or interval[1] <= now:
        return []
    # Deduplicate members even if a caller supplies multiple profile rows.
    return sorted({
        player["member_id"] for player in players
        if covers_interval(player["answers"], *interval)
    })


def source_order(key):
    """Snowflakes determine original post order across all collected channels."""
    message_id, position = key.split(":")
    return int(message_id), int(position)


def source_author(offer, key):
    return str(offer.get("authorID") or f"source:{key.split(':')[0]}")


def select_offers(offers):
    """Newest qualifying post per exact start, counting author/start bumps once."""
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


class OpportunityPublisher:
    def __init__(self, bot, store, feed, forms, guild_id, channel_id):
        self.bot, self.store, self.feed, self.forms = bot, store, feed, forms
        self.guild_id, self.channel_id = guild_id, channel_id
        self.lock = asyncio.Lock()

    def register_views(self):
        for key, post in self.store.opportunity_posts(self.channel_id).items():
            if post["message_id"] and post["status"] == "active":
                self.bot.add_view(OpportunityVotes(self, key), message_id=post["message_id"])

    def eligible_players(self, guild, form):
        role_ids = {form[k] for k in ("tryout_role", "team_role", "manager_role")}
        return [p for p in self.store.scrim_candidates(self.guild_id, "marvel-rivals", form["version"])
                if (member := guild.get_member(p["member_id"])) is not None and not member.bot
                and any(role.id in role_ids for role in member.roles)]

    async def sync(self, now=None):
        async with self.lock:
            await self._sync(now)

    async def _sync(self, now=None):
        now = time.time() if now is None else now
        guild = self.bot.get_guild(self.guild_id)
        form = self.forms.get("marvel-rivals")
        if guild is None or form is None:
            return
        channel = guild.get_channel(self.channel_id)
        if channel is None:
            log.warning("Scrim opportunity channel is unavailable")
            return
        players = self.eligible_players(guild, form)
        offers, revisions = self.feed.opportunity_snapshot()
        ranks = self.store.scrim_rank_filter(self.channel_id)
        posts = self.store.opportunity_posts(self.channel_id)
        matched = {key: match_offer(offer, players, now) for key, offer in offers.items()}
        qualified = {key: offer for key, offer in offers.items()
                     if len(matched[key]) >= MIN_AVAILABLE_PLAYERS and rank_matches(offer, ranks)}
        selected = select_offers(qualified)
        # Keep a stable channel-message/button identity while its displayed source changes.
        anchors = {}
        for key, post in sorted(posts.items(), key=lambda item: (
                item[1]["status"] == "inactive", item[1]["message_id"] is None,
                item[1]["pending_since"], item[0])):
            start = post_start(key, post, offers)
            if start in selected:
                anchors.setdefault(start, key)
        # Finder posts stop accepting interest at their advertised start.
        selected = {start: source for start, source in selected.items() if start > now}
        anchors = {start: key for start, key in anchors.items() if start in selected}
        pending = [p for p in posts.values() if p["message_id"] is None and p["status"] == "pending"]
        recovered = {}
        if pending:
            # Recover an acknowledged-late send after a crash before its ID was saved.
            after = datetime.fromtimestamp(min(p["pending_since"] for p in pending) - 5, UTC)
            async for message in channel.history(after=after, limit=None):
                if message.author.id == self.bot.user.id:
                    for embed in message.embeds:
                        marker = embed.footer.text or ""
                        if marker.startswith("Scrim finder • "):
                            recovered[marker.removeprefix("Scrim finder • ")] = message
        # Retire expired slots and any duplicates left by the old per-source publisher.
        # Failed deletion blocks delivery until retry, preserving one message per start.
        for key, previous in posts.items():
            if previous["status"] == "inactive" or key in anchors.values():
                continue
            try:
                message = recovered.pop(key, None)
                if message is None and previous["message_id"]:
                    try:
                        message = await channel.fetch_message(previous["message_id"])
                    except discord.NotFound:
                        pass
                if message is not None:
                    try:
                        await message.delete()
                    except discord.NotFound:
                        pass
                self.store.save_opportunity(self.channel_id, key, None, None, "inactive", reset_votes=True)
            except discord.HTTPException:
                log.warning("Scrim opportunity withdrawal failed; will retry", exc_info=False)
                return
        for start, (source_key, count) in sorted(selected.items()):
            offer, members = offers[source_key], matched[source_key]
            key = anchors.get(start)
            if key is None:
                # Preserve legacy identities where possible; a source moving to a
                # different start must not steal the previous slot's message mapping.
                key = source_key if source_key not in posts else f"time:{start}"
            previous = posts.get(key)
            author = source_author(offer, source_key)
            # Votes belong to this start-time message and survive source replacements.
            self.store.clear_opportunity_votes(self.channel_id, key, {p["member_id"] for p in players})
            votes = self.store.opportunity_votes(self.channel_id, key)
            embed = matched_offer_embed(offer, members, key, votes, now=now, scrim_count=count)
            content = " ".join(f"<@{mid}>" for mid in members)
            fingerprint = hashlib.sha256(
                json.dumps([content, embed.to_dict()], sort_keys=True).encode()
            ).hexdigest()
            if (previous and previous["fingerprint"] == fingerprint
                    and previous["status"] == "active"):
                if (previous["start_time"], previous["source_key"], previous["source_revision"],
                        previous["source_author"]) != (start, source_key, revisions[source_key], author):
                    self.store.save_opportunity(
                        self.channel_id, key, previous["message_id"], fingerprint, "active", start,
                        source_key, revisions[source_key], author,
                    )
                continue
            try:
                message = recovered.get(key)
                if message is None and previous and previous["message_id"]:
                    try:
                        message = await channel.fetch_message(previous["message_id"])
                    except discord.NotFound:
                        pass
                if message is not None:
                    await message.edit(content=content, embed=embed, view=OpportunityVotes(self, key),
                                       allowed_mentions=discord.AllowedMentions.none())
                else:
                    self.store.reserve_opportunity(self.channel_id, key, start, source_key,
                                                   revisions[source_key], author)
                    message = await channel.send(
                        content=content, embed=embed, view=OpportunityVotes(self, key),
                        allowed_mentions=discord.AllowedMentions(
                            users=[discord.Object(mid) for mid in members],
                            roles=False, everyone=False,
                        ),
                    )
                self.store.save_opportunity(
                    self.channel_id, key, message.id, fingerprint, "active", start, source_key,
                    revisions[source_key], author,
                )
            except discord.HTTPException:
                log.warning("Scrim opportunity delivery failed; will retry", exc_info=False)
        await self.sync_vote_summary(channel, now)

    async def sync_vote_summary(self, channel, now=None):
        now = time.time() if now is None else now
        ranked = [(len(self.store.opportunity_votes(self.channel_id, key)), post)
                  for key, post in self.store.opportunity_posts(self.channel_id).items()
                  if post["status"] == "active" and post["message_id"]
                  and post["start_time"] is not None and post["start_time"] > now]
        maximum = max((votes for votes, _ in ranked), default=0)
        leaders = sorted((post for votes, post in ranked if votes == maximum),
                         key=lambda post: (post["start_time"], post["message_id"]))
        links = "\n".join(
            f"**{maximum}/6 votes** · <t:{post['start_time']}:f> · [Jump to message](https://discord.com/channels/"
            f"{self.guild_id}/{self.channel_id}/{post['message_id']})" for post in leaders
        )
        embed = discord.Embed(title="Most voted scrim", description=(
            links if leaders else "No upcoming scrim posts."
        ))
        marker = "Scrim finder vote summary"
        embed.set_footer(text=marker)
        saved = self.store.vote_summary_message(self.channel_id)
        message = None
        try:
            if saved:
                try:
                    message = await channel.fetch_message(saved)
                except discord.NotFound:
                    pass
            latest = None
            # Also recover a send acknowledged after a crash, and remove duplicates.
            async for candidate in channel.history(limit=100):
                if latest is None:
                    latest = candidate.id
                if candidate.author.id == self.bot.user.id and any(
                        e.footer.text == marker for e in candidate.embeds):
                    if message is None:
                        message = candidate
                    elif candidate.id != message.id:
                        await candidate.delete()
            if message is not None and message.id != latest:
                await message.delete()
                message = None
            if message is None:
                message = await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
            elif not message.embeds or message.embeds[0].to_dict() != embed.to_dict():
                await message.edit(embed=embed, allowed_mentions=discord.AllowedMentions.none())
            self.store.save_vote_summary_message(self.channel_id, message.id)
        except discord.HTTPException:
            log.warning("Scrim vote summary update failed; will retry", exc_info=False)

    async def vote(self, interaction, key, add):
        await interaction.response.defer(ephemeral=True)
        async with self.lock:
            guild = self.bot.get_guild(self.guild_id)
            form = self.forms.get("marvel-rivals")
            players = self.eligible_players(guild, form) if guild and form else []
            if (interaction.guild_id != self.guild_id or interaction.channel_id != self.channel_id
                    or interaction.user.id not in {p["member_id"] for p in players}):
                await interaction.followup.send(
                    "Only Marvel Rivals tryouts, team members, and managers with a completed current form "
                    "can vote.", ephemeral=True,
                )
                return
            post = self.store.opportunity_posts(self.channel_id).get(key)
            offers, revisions = self.feed.opportunity_snapshot()
            source_key = (post["source_key"] or key) if post else key
            offer = offers.get(source_key)
            members = match_offer(offer, players, time.time()) if offer else []
            ranks = self.store.scrim_rank_filter(self.channel_id)
            qualified = {k: o for k, o in offers.items()
                         if len(match_offer(o, players, time.time())) >= MIN_AVAILABLE_PLAYERS
                         and rank_matches(o, ranks)}
            selected = select_offers(qualified)
            current = selected.get(post_start(key, post, offers)) if post else None
            if (not post or post["status"] != "active" or post["message_id"] != interaction.message.id
                    or len(members) < MIN_AVAILABLE_PLAYERS
                    or not rank_matches(offer, ranks) or not current or current[0] != source_key
                    or post["source_revision"] not in (None, revisions.get(source_key))):
                await interaction.followup.send(
                    "This scrim opportunity is no longer available.", ephemeral=True
                )
                return
            self.store.clear_opportunity_votes(self.channel_id, key, {p["member_id"] for p in players})
            start = post_start(key, post, offers)
            if start <= time.time():
                await self._sync()
                await interaction.followup.send(
                    "This scrim opportunity is no longer available.", ephemeral=True
                )
                return
            try:
                self.store.vote_opportunity(self.channel_id, key, interaction.user.id, add)
            except ValueError as exc:
                await interaction.followup.send(str(exc), ephemeral=True)
                return
            # Refresh the public count from durable votes; periodic sync retries a failed edit.
            await self._sync()
        await interaction.followup.send("Your vote is saved." if add else "Your vote has been removed.",
                                        ephemeral=True)
