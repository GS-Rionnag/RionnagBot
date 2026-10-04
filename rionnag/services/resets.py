"""Versioned resets and reconciliation. Persist recovery intent before removing roles."""

import logging

import discord

from rionnag import config
from rionnag.services.applications import COMPLETE
from rionnag.services.permissions import game_member, removable_roles

log = logging.getLogger(__name__)


class Resets:
    def __init__(self, applications):
        self.app = applications
        self.store = applications.store

    async def reset_member(self, member, game):
        async with self.app.lock(member.id):
            row = self.store.member(member.id)
            target = self.app.forms[game]["version"]
            if row["restore_roles"] is None and row["status"] != "reset":
                roles = removable_roles(member)
                approved = row["status"] == "accepted" or game_member(member, self.app.forms)
                # Pending/rejected data is prefilled but never gains automatic approval.
                self.store.update(
                    member.id,
                    status="reset",
                    game=game,
                    reset_target=target,
                    restore_roles=[role.id for role in roles] if approved else None,
                    restore_status=row["status"] if approved and row["status"] in COMPLETE else "accepted",
                )
            else:
                self.store.update(member.id, reset_target=target)
            roles = removable_roles(member)
            unmanageable = [role for role in roles if role >= member.guild.me.top_role]
            if unmanageable:
                raise ValueError("Move the bot role above all membership roles before resetting.")
            if roles:
                await member.remove_roles(*roles, reason="Game form changed: updated onboarding required")
            self.store.remove_profile(member.guild.id, member.id)
            await self.app.welcome(member)

    async def reconcile_member(self, member):
        async with self.app.lock(member.id):
            row = self.store.member(member.id)
            if row["status"] == "deciding":
                await self.app.finish_decision(member)
            elif row["status"] == "pending":
                channel = await self.app.ticket(member)
                await self.app.publish(member, channel)
            elif row["status"] in COMPLETE:
                # Remove stale channels left by interrupted completion, restore saved membership on rejoin.
                if row["status"] in {"visitor", "rejected"}:
                    await member.add_roles(
                        member.guild.get_role(config.VISITOR_ROLE_ID), reason="Restore visitor membership"
                    )
                elif row["game"]:
                    form = self.app.forms[row["game"]]
                    if not game_member(member, self.app.forms):
                        role_ids = row["membership_roles"] or [form["tryout_role"]]
                        roles = [member.guild.get_role(role_id) for role_id in role_ids]
                        if any(role is None for role in roles):
                            raise ValueError("A membership role was deleted; owner repair is required.")
                        await member.add_roles(*roles, reason="Restore completed membership on rejoin")
                    else:
                        self.store.update(
                            member.id,
                            membership_roles=[
                                role.id
                                for role in removable_roles(member)
                                if role.id != config.VISITOR_ROLE_ID
                            ],
                        )
                    visitor = member.guild.get_role(config.VISITOR_ROLE_ID)
                    if visitor in member.roles:
                        await member.remove_roles(visitor, reason="Team members cannot access tryout entry")
                await self.app.notify(member)
                await self.app.close_ticket(member)
            else:
                # A member with roles but no completed data still must onboard. Includes the owner.
                roles = removable_roles(member)
                if row["status"] == "new" and game_member(member, self.app.forms):
                    game = next(
                        key
                        for key, form in self.app.forms.items()
                        if any(
                            role.id in {form["team_role"], form["tryout_role"], form["manager_role"]}
                            for role in member.roles
                        )
                    )
                    # Fresh role metadata from live Discord, never from the deleted database.
                    self.store.update(
                        member.id,
                        status="reset",
                        game=game,
                        restore_roles=[role.id for role in roles],
                        restore_status="accepted",
                        reset_target=self.app.forms[game]["version"],
                    )
                if roles:
                    await member.remove_roles(*roles, reason="No completed onboarding data")
                await self.app.welcome(member)

    async def reconcile(self, guild):
        for key, form in self.app.forms.items():
            self.store.check_form(key, form)
        members = [member async for member in guild.fetch_members(limit=None) if not member.bot]
        failed = []
        for member in members:
            try:
                row = self.store.member(member.id)
                form = self.app.form_for(row)
                if form and row["version"] > 0 and row["version"] < form["version"]:
                    await self.reset_member(member, row["game"])
                await self.reconcile_member(member)
            except Exception:
                log.exception("Could not reconcile onboarding for member %s", member.id)
                failed.append(member.id)
        # Prune abandoned bot tickets (including tickets predating the database wipe).
        active_ids = {member.id for member in members}
        # Catch departures missed while the bot was offline.
        for member_id in set(self.store.ids()) - active_ids:
            async with self.app.lock(member_id):
                self.store.delete_member(guild.id, member_id)
        category = guild.get_channel(config.APPLICATIONS_CATEGORY_ID)
        if category:
            registered = {self.store.member(mid)["channel_id"] for mid in active_ids}
            for channel in list(category.channels):
                if channel.id not in registered and isinstance(channel, discord.TextChannel):
                    await channel.delete(reason="Obsolete onboarding ticket after rebuild")
        for key, form in self.app.forms.items():
            self.store.record_form(key, form)
        if failed:
            raise RuntimeError(f"Onboarding reconciliation failed for {len(failed)} members; see logs.")
