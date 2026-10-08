"""Explicit owner-account outbound requests; uncertain sends never retry blindly."""

import asyncio
import json
import logging
import os
import re
import time
from datetime import UTC, datetime

import discord

from rionnag import config
from rionnag.scrims.scrims import normalized_roles
from rionnag.services.scrim_availability import covers_interval
from rionnag.storage import Store

log = logging.getLogger(__name__)


def invalid_request(store, job, players, live_members, owner_id, manager_role):
    """Return a reason instead of ever sending an expired or broken lineup."""
    if job["start"] <= time.time():
        return "Session already started"
    settings = store.host_settings(job["channel_id"])
    content_matches = bool(re.fullmatch(
        rf"LFS [A-Za-z0-9 ]+ - [A-Za-z0-9 ]+ at <t:{job['start']}:F>", job["content"]
    )) or job["content"].splitlines()[0] == f"LFS at <t:{job['start']}:F>"
    if (
        settings["duration"] != job["duration"]
        or settings["destination"] != job["destination"]
        or not content_matches
    ):
        return "Hosting settings changed; review a new session"
    requester = live_members.get(job["requested_by"])
    if requester is None or not (
        requester.id == owner_id or any(r.id == manager_role for r in requester.roles)
    ):
        return "Requester is no longer authorized"
    lineup = {int(mid): role for mid, role in json.loads(job["lineup"]).items()}
    if len(lineup) != 6 or any(list(lineup.values()).count(role) != 2 for role in ("Tank", "DPS", "Support")):
        return "Invalid composition"
    votes = store.host_votes(job["channel_id"]).get(job["start"], set())
    for mid, role in lineup.items():
        player, member = players.get(mid), live_members.get(mid)
        if not player or not member or member.bot or mid not in votes:
            return "A starter is no longer eligible or confirmed"
        if any(r.id == config.VISITOR_ROLE_ID for r in member.roles):
            return "A starter is now a Visitor"
        form = config.load_forms()["marvel-rivals"]
        if not any(
            r.id in {form[k] for k in ("team_role", "tryout_role", "manager_role")} for r in member.roles
        ):
            return "A starter no longer has a game role"
        answers = player["answers"]
        if role not in normalized_roles([answers.get("preferred_role_1"), answers.get("preferred_role_2")]):
            return "A starter's preferred roles changed"
        overrides = store.host_overrides(job["channel_id"], job["duration"]).get(job["start"], set())
        if mid not in overrides and not covers_interval(
            answers, job["start"], job["start"] + job["duration"]
        ):
            return "A starter's availability changed"
    return None


async def process_job(client, store, job, allowed):
    key = job["channel_id"], job["start"]
    if job["destination"] not in allowed:
        state = "uncertain" if job["status"] in {"sending", "uncertain"} else "failed"
        store.update_host_advert(
            *key, state, error="Destination is not allowed; check any prior delivery manually"
        )
        return
    channel = client.get_channel(job["destination"]) or await client.fetch_channel(job["destination"])
    if job["status"] in {"sending", "uncertain"}:
        # Resolve a crash/network timeout by checking the original account's history.
        # No match is not proof of failed delivery; never automatically resend.
        after = datetime.fromtimestamp(job["requested_at"] - 5, UTC)
        async for message in channel.history(limit=None, after=after):
            if message.author.id == client.user.id and message.content == job["content"]:
                store.update_host_advert(*key, "sent", message_id=message.id)
                return
        store.update_host_advert(*key, "uncertain", error="Delivery unconfirmed; check destination manually")
        return
    guild = client.get_guild(config.GUILD_ID)
    if guild is None or client.user.id != guild.owner_id:
        store.update_host_advert(*key, "failed", error="Collector must be the server owner's account")
        return
    form = config.load_forms()["marvel-rivals"]
    players = {
        p["member_id"]: p for p in store.scrim_candidates(config.GUILD_ID, "marvel-rivals", form["version"])
    }
    ids = {int(mid) for mid in json.loads(job["lineup"])} | {job["requested_by"]}
    members = {}
    for mid in ids:
        try:
            members[mid] = await guild.fetch_member(mid)
        except discord.NotFound:
            pass
    # Refresh votes/profiles after network reads, immediately before claiming the job.
    players = {
        p["member_id"]: p for p in store.scrim_candidates(config.GUILD_ID, "marvel-rivals", form["version"])
    }
    reason = invalid_request(store, job, players, members, guild.owner_id, form["manager_role"])
    if reason:
        store.update_host_advert(*key, "failed", error=reason)
        return
    if not store.claim_host_advert(*key):
        return
    try:
        message = await channel.send(
            job["content"], nonce=str(job["start"]), allowed_mentions=discord.AllowedMentions.none()
        )
    except (discord.HTTPException, OSError, TimeoutError):
        store.update_host_advert(*key, "uncertain", error="Delivery unconfirmed; checking destination")
        return
    store.update_host_advert(*key, "sent", message_id=message.id)


async def publish_requests(client):
    await client.wait_until_ready()
    store = Store(config.DATABASE)
    while not client.is_closed():
        raw = os.getenv("SCRIM_HOST_CHANNEL_IDS") or os.getenv("SCRIM_SOURCE_CHANNEL_IDS", "")
        allowed = {int(cid.strip()) for cid in raw.split(",") if cid.strip().isdigit()}
        for job in store.host_adverts(config.SCRIM_HOST_CHANNEL_ID).values():
            if job["status"] not in {"queued", "sending", "uncertain"}:
                continue
            try:
                await process_job(client, store, job, allowed)
            except Exception:
                log.warning("Hosting delivery check failed; retained for review/retry", exc_info=False)
        await asyncio.sleep(10)
