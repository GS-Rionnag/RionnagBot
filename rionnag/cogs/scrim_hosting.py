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
        self.service.register_approvals()
        self.refresh.start()
        self.approvals.start()

    async def cog_unload(self):
        self.refresh.cancel()
        self.approvals.cancel()

    @tasks.loop(seconds=10)
    async def approvals(self):
        try:
            await self.service.notify_ready()
        except Exception:
            logging.getLogger(__name__).exception("Scrim owner notification failed; will retry")

    @approvals.before_loop
    async def before_approval_refresh(self):
        await self.service.bot.wait_until_ready()

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
