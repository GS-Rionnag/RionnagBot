"""The Third Eye: webhook-based uwu locks, isolated from organization workflows."""

import discord

from rionnag.servers.third_eye.cogs.uwu import UwuLocks


async def setup(bot, profile):
    await bot.add_cog(UwuLocks(bot, profile.data_dir / "locks.sqlite3"),
                      guild=discord.Object(id=profile.guild_id))
