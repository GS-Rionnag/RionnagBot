import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands, tasks

from rionnag import config
from rionnag.services.permissions import apply_server_policy
from rionnag.services.resets import Resets
from rionnag.ui.onboarding import EntryView, ReviewView, WelcomeView, report

log = logging.getLogger(__name__)


class Onboarding(commands.Cog):
    def __init__(self, bot, service):
        self.bot, self.service = bot, service
        self.resets = Resets(service)
        self.ready = False
        self.reconcile_lock = asyncio.Lock()

    async def cog_load(self):
        self.bot.add_view(WelcomeView(self.service))
        self.bot.add_view(ReviewView(self.service))
        self.repair.start()

    async def cog_unload(self):
        self.repair.cancel()

    async def reconcile(self):
        async with self.reconcile_lock:
            guild = self.bot.get_guild(config.GUILD_ID)
            if guild is None:
                raise RuntimeError("Rionnag guild is unavailable.")
            # Validate before any server mutations.
            for key, form in self.service.forms.items():
                self.service.store.check_form(key, form)
                for role_key in ("team_role", "tryout_role", "manager_role"):
                    if guild.get_role(form[role_key]) is None:
                        raise ValueError(f"Missing {role_key} for {key}")
            await apply_server_policy(guild, self.service.forms, self.service.store)
            await self.resets.reconcile(guild)
            entry = guild.get_channel(config.ENTRY_CHANNEL_ID)
            async for message in entry.history(limit=100):
                if message.author == self.bot.user and message.components:
                    await message.edit(
                        content="Visitors: choose a game to apply for a tryout.",
                        embed=None,
                        view=EntryView(self.service),
                    )
                    break
            else:
                await entry.send(
                    "Visitors: choose a game to apply for a tryout.", view=EntryView(self.service)
                )
            self.ready = True
            log.info("Onboarding reconciliation complete")

    @tasks.loop(minutes=5)
    async def repair(self):
        try:
            await self.reconcile()
        except Exception:
            log.exception("Onboarding reconciliation failed; retrying in five minutes")

    @repair.before_loop
    async def before_repair(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_member_join(self, member):
        if member.guild.id != config.GUILD_ID or member.bot:
            return
        row = self.service.store.member(member.id)
        form = self.service.form_for(row)
        if form and row["version"] < form["version"] and row["answers"].get("username"):
            await self.resets.reset_member(member, row["game"])
        await self.resets.reconcile_member(member)

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        if member.guild.id == config.GUILD_ID and not member.bot:
            async with self.service.lock(member.id):
                await self.service.close_ticket(member)

    @app_commands.command(
        name="reload_forms", description="Owner: load updated forms and reset outdated members"
    )
    async def reload_forms(self, interaction: discord.Interaction):
        if interaction.user.id != interaction.guild.owner_id:
            raise ValueError("Only the server owner can reload forms.")
        await interaction.response.defer(ephemeral=True, thinking=True)
        forms = config.load_forms()
        if set(self.service.forms) - set(forms):
            raise ValueError("Do not remove games with stored member data.")
        for key, form in forms.items():
            self.service.store.check_form(key, form)
        self.service.forms = forms
        self.bot.add_view(WelcomeView(self.service))
        await self.reconcile()
        await interaction.followup.send(
            "Forms reloaded. Outdated members have private onboarding tickets.", ephemeral=True
        )

    @app_commands.command(name="onboard", description="Owner: repair unfinished member onboarding")
    async def onboard(self, interaction: discord.Interaction, member: discord.Member):
        if interaction.user.id != interaction.guild.owner_id:
            raise ValueError("Only the server owner can repair onboarding.")
        await interaction.response.defer(ephemeral=True)
        await self.resets.reconcile_member(member)
        await interaction.followup.send("Member onboarding reconciled.", ephemeral=True)

    async def cog_app_command_error(self, interaction, error):
        await report(interaction, getattr(error, "original", error))
