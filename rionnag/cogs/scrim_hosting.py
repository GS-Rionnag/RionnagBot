"""Hosting commands and refresh lifecycle, independent of the existing finder."""

import logging

import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import dotenv_values

from rionnag import config
from rionnag.ui.scrim_hosting import DayPicker, HostingBoard


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
        self.invitations.start()
        self.official_notifications.start()

    async def cog_unload(self):
        self.refresh.cancel()
        self.approvals.cancel()
        self.invitations.cancel()
        self.official_notifications.cancel()

    @tasks.loop(seconds=30)
    async def official_notifications(self):
        try:
            await self.service.notify_official()
        except Exception:
            logging.getLogger(__name__).exception("Official scrim notification failed; will retry")

    @official_notifications.before_loop
    async def before_official_notifications(self):
        await self.service.bot.wait_until_ready()

    @tasks.loop(seconds=10)
    async def invitations(self):
        try:
            await self.service.invite_available()
        except Exception:
            logging.getLogger(__name__).exception("Scrim player invitation failed; will retry")

    @invitations.before_loop
    async def before_invitations(self):
        await self.service.bot.wait_until_ready()

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
            await self.service.refresh_owner_panels()
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

    @app_commands.command(name="scrim_host_for", description="View scrim day and time options for a member")
    @app_commands.describe(member="Member whose saved availability to use")
    async def board_for(self, interaction: discord.Interaction, member: discord.Member):
        if interaction.guild_id != config.GUILD_ID:
            await interaction.response.send_message("Use this command in the Rionnag server.", ephemeral=True)
            return
        if not any(p["member_id"] == member.id for p in self.service.players()):
            await interaction.response.send_message(
                "That member does not have a current eligible Marvel Rivals profile.", ephemeral=True
            )
            return
        slots = [s for s in self.service.snapshot() if member.id in s.available]
        if not slots:
            await interaction.response.send_message(
                "No matching scrim sessions fit that member's saved availability yet.", ephemeral=True
            )
            return
        view = DayPicker(self.service, interaction.user.id, slots, "inspect", subject=member.id)
        await interaction.response.send_message(embed=view.embed(), view=view, ephemeral=True)
