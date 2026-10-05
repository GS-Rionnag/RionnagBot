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
from rionnag.ui.scrim_opportunities import OpportunityVotes, matched_offer_embed

log = logging.getLogger(__name__)
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
        offers = self.feed.identified_offers()
        posts = self.store.opportunity_posts(self.channel_id)
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
        for key in sorted(offers.keys() | posts.keys()):
            offer = offers.get(key)
            members = match_offer(offer, players, now) if offer else []
            eligible = len(members) >= 4
            previous = posts.get(key)
            if not eligible and previous is None:
                continue
            if not eligible and previous and previous["status"] == "inactive":
                continue
            self.store.clear_opportunity_votes(self.channel_id, key, {p["member_id"] for p in players})
            if eligible:
                votes = self.store.opportunity_votes(self.channel_id, key)
                embed = matched_offer_embed(offer, members, key, votes)
                content = " ".join(f"<@{mid}>" for mid in members)
                fingerprint = hashlib.sha256(
                    json.dumps([content, embed.to_dict()], sort_keys=True).encode()
                ).hexdigest()
            else:
                fingerprint = None
            if (eligible and previous and previous["fingerprint"] == fingerprint
                    and previous["status"] == "active"):
                continue
            try:
                message = recovered.get(key)
                if message is None and previous and previous["message_id"]:
                    try:
                        message = await channel.fetch_message(previous["message_id"])
                    except discord.NotFound:
                        pass
                if message is not None:
                    if eligible:
                        await message.edit(content=content, embed=embed, view=OpportunityVotes(self, key),
                                           allowed_mentions=discord.AllowedMentions.none())
                    else:
                        try:
                            await message.delete()
                        except discord.NotFound:
                            pass
                elif eligible:
                    self.store.reserve_opportunity(self.channel_id, key)
                    message = await channel.send(
                        content=content, embed=embed, view=OpportunityVotes(self, key),
                        allowed_mentions=discord.AllowedMentions(
                            users=[discord.Object(mid) for mid in members], roles=False, everyone=False
                        ),
                    )
                if not eligible:
                    self.store.clear_opportunity_votes(self.channel_id, key)
                self.store.save_opportunity(
                    self.channel_id, key, message.id if message and eligible else None, fingerprint,
                    "active" if eligible else "inactive",
                )
            except discord.HTTPException:
                log.warning("Scrim opportunity delivery failed; will retry", exc_info=False)

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
            offer = self.feed.identified_offers().get(key)
            members = match_offer(offer, players, time.time()) if offer else []
            if (not post or post["status"] != "active" or post["message_id"] != interaction.message.id
                    or len(members) < 4):
                await interaction.followup.send(
                    "This scrim opportunity is no longer available.", ephemeral=True
                )
                return
            self.store.clear_opportunity_votes(self.channel_id, key, {p["member_id"] for p in players})
            try:
                self.store.vote_opportunity(self.channel_id, key, interaction.user.id, add)
            except ValueError as exc:
                await interaction.followup.send(str(exc), ephemeral=True)
                return
            # Refresh the public count from durable votes; periodic sync retries a failed edit.
            await self._sync()
        await interaction.followup.send("Your vote is saved." if add else "Your vote has been removed.",
                                        ephemeral=True)
