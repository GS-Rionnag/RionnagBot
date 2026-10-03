import asyncio

import discord
from discord import app_commands
from discord.ext import commands

from rionnag import config
from rionnag.integrations.rivals import (
    add_hero_fields,
    fetch_player_overview,
    profile_overview,
    queued_lookup,
)
from rionnag.services.permissions import has_role
from rionnag.ui.onboarding import report


class Profiles(commands.Cog):
    def __init__(self, service):
        self.service = service

    @app_commands.command(name="profile", description="View a saved member’s Marvel Rivals profile")
    async def profile(self, interaction: discord.Interaction, member: discord.Member | None = None):
        member = member or interaction.user
        saved = self.service.store.saved_profile(interaction.guild_id, member.id)
        if not saved:
            raise ValueError("This member has no completed game profile.")
        await interaction.response.defer(ephemeral=True, thinking=True)
        player = await asyncio.to_thread(queued_lookup, fetch_player_overview, saved[0])
        fields, icon = profile_overview(player)
        embed = discord.Embed(title=f"{member.display_name} · Marvel Rivals", color=config.COLOR)
        embed.add_field(name="Username", value=saved[0])
        embed.add_field(name="Time zone", value=saved[1])
        embed.add_field(name="Preferred roles", value=f"{saved[2]} / {saved[3]}")
        for label, key in (
            ("Current rank", "current_rank"),
            ("Peak rank", "peak_rank"),
            ("Current season win rate", "overall_win_rate"),
        ):
            embed.add_field(name=label, value=fields[key])
        add_hero_fields(embed, fields)
        if icon:
            embed.set_image(url=icon)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(
        name="promote", description="Game manager: promote a tryout to team, or team to manager"
    )
    async def promote(self, interaction: discord.Interaction, member: discord.Member):
        form = self.service.forms["marvel-rivals"]
        reviewer = await interaction.guild.fetch_member(interaction.user.id)
        if not has_role(reviewer, form["manager_role"]):
            raise ValueError("Only Marvel Rivals Managers can promote members.")
        async with self.service.lock(member.id):
            member = await interaction.guild.fetch_member(member.id)
            row = self.service.store.member(member.id)
            if row["status"] != "accepted" or row["version"] != form["version"]:
                raise ValueError("This member must finish their current game form first.")
            if has_role(member, form["manager_role"]):
                raise ValueError("This member is already a manager.")
            old, new = (
                ("team_role", "manager_role")
                if has_role(member, form["team_role"])
                else ("tryout_role", "team_role")
            )
            if not has_role(member, form[old]):
                raise ValueError("This member does not have a role that can be promoted.")
            await interaction.response.defer(ephemeral=True)
            await member.add_roles(interaction.guild.get_role(form[new]), reason="Game manager promotion")
            await member.remove_roles(interaction.guild.get_role(form[old]), reason="Game manager promotion")
            role_ids = [
                role.id
                for role in member.roles
                if role.id not in {form[old], config.VISITOR_ROLE_ID}
                and not role.is_default()
                and not role.managed
                and role.id != config.OWNER_ROLE_ID
            ]
            self.service.store.update(member.id, membership_roles=list(set(role_ids + [form[new]])))
            await interaction.followup.send(f"Promoted {member.mention}.", ephemeral=True)

    async def cog_app_command_error(self, interaction, error):
        await report(interaction, getattr(error, "original", error))
