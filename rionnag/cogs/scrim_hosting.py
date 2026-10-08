"""Hosting commands and refresh lifecycle, independent of the existing finder."""

import logging

import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import dotenv_values

from rionnag import config
from rionnag.ui.scrim_hosting import HostingBoard


def posting_channels():
    values = dotenv_values(config.ROOT / "scrim_collector" / ".env")
    # Explicit outbound allowlist; falls back to the already configured scrim sources.
    raw = values.get("SCRIM_HOST_CHANNEL_IDS") or values.get("SCRIM_SOURCE_CHANNEL_IDS") or ""
    return {int(cid.strip()) for cid in raw.split(",") if cid.strip().isdigit()}


class ScrimHosting(commands.Cog):
    def __init__(self, service):
        self.service = service

    async def cog_load(self):
        self.service.bot.add_view(HostingBoard(self.service))
        self.refresh.start()

    async def cog_unload(self):
        self.refresh.cancel()

    @tasks.loop(seconds=60)
    async def refresh(self):
        try:
            await self.service.sync()
        except Exception:
            logging.getLogger(__name__).exception("Hosting refresh failed; will retry")

    @refresh.before_loop
    async def before_refresh(self):
        await self.service.bot.wait_until_ready()

    @app_commands.command(name="scrim_host", description="Open the scrim hosting board")
    async def board(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            f"Choose and confirm sessions in <#{self.service.channel_id}>.", ephemeral=True
        )

    @app_commands.command(
        name="scrim_host_settings", description="Managers: set hosting duration and advert destination"
    )
    @app_commands.describe(
        destination="Advert channel ID in a scrim server; omit to keep the saved destination",
        duration_minutes="Full session length, 60–240 minutes; default 120",
    )
    async def settings(
        self,
        interaction: discord.Interaction,
        destination: str | None = None,
        duration_minutes: app_commands.Range[int, 60, 240] = 120,
    ):
        if not self.service.manager(interaction):
            await interaction.response.send_message(
                "Only the owner or Marvel Rivals Managers can configure hosting.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        try:
            await self.service.configure(destination, duration_minutes)
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        await interaction.followup.send(
            "Hosting settings saved. A changed session length clears confirmations. "
            "Nothing is published until you confirm a preview.",
            ephemeral=True,
        )
