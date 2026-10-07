import asyncio
import math

import discord
from discord import app_commands
from discord.ext import commands

from rionnag import config
from rionnag.services.health import recent_errors


class Health(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="ping", description="Show bot latency and recent error counts privately")
    async def ping(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        latency = self.bot.latency
        ping = f"{latency * 1000:.0f} ms" if math.isfinite(latency) else "Unavailable"
        errors = await asyncio.to_thread(recent_errors, config.ROOT / "logs" / "bot.log")
        await interaction.followup.send(
            f"**Bot gateway ping:** {ping}\n**Recent bot errors:**\n{errors}",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )
