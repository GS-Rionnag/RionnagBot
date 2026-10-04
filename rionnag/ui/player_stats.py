"""Render the existing public Rivals lookup as a separate application embed."""

import asyncio
import logging

import discord

from rionnag import config
from rionnag.integrations.rivals import fetch_player_overview, profile_overview, queued_lookup

log = logging.getLogger(__name__)
STATS_VERSION = 9


async def player_stats_embed(answers):
    identity = str(answers.get("player_uid") or answers["username"])
    cached = answers.get("rivals_stats_embed")
    if (
        cached
        and answers.get("rivals_stats_uid") == identity
        and answers.get("rivals_stats_version") == STATS_VERSION
    ):
        return discord.Embed.from_dict(cached)
    embed = discord.Embed(title="Marvel Rivals player data", color=config.COLOR)
    try:
        player = await asyncio.wait_for(
            asyncio.to_thread(queued_lookup, fetch_player_overview, identity), 300
        )
        fields, icon = profile_overview(player)
        embed.add_field(name="Player", value=discord.utils.escape_markdown(player["player_name"])[:100])
        embed.add_field(name="Account UID", value=player["player_uid"])
        for label, key in (
            ("Current Season Rank", "current_rank"),
            ("Peak Rank", "peak_rank"),
            (
                fields.get("overall_win_rates_name", "Current Season Win Rate"),
                "overall_win_rate",
            ),
        ):
            embed.add_field(name=label, value=fields[key][:256])
        embed.add_field(
            name=fields["role_win_rates_name"], value=fields["role_win_rates"][:700], inline=False
        )
        embed.add_field(
            name=fields.get("hero_win_rates_name", "Top 6 Characters (Current Season)"),
            value=fields.get("competitive_heroes", fields["top_characters"])[:1024],
            inline=False,
        )
        if icon:
            embed.set_thumbnail(url=icon)
        if fields.get("win_rate_note"):
            embed.set_footer(text=fields["win_rate_note"][:300])
    except Exception:
        log.exception("Application player stats lookup failed")
        embed.description = (
            "Player data is temporarily unavailable. Your application has still been submitted."
        )
        return embed
    if player.get("match_hero_rates") and player.get("match_class_rates") and not player.get("win_rate_note"):
        answers["rivals_stats_embed"] = embed.to_dict()
        answers["rivals_stats_uid"] = identity
        answers["rivals_stats_version"] = STATS_VERSION
    return embed
