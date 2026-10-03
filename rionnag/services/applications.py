from __future__ import annotations

import asyncio
import io
import logging

import discord

from rionnag import config
from rionnag.services.permissions import has_role, ticket_overwrites, visitor_eligible

log = logging.getLogger(__name__)
COMPLETE = {"visitor", "accepted", "rejected"}


class Applications:
    def __init__(self, bot, store, forms):
        self.bot, self.store, self.forms = bot, store, forms
        self.locks = {}
        self.account_lock = asyncio.Lock()

    def lock(self, member_id):
        return self.locks.setdefault(member_id, asyncio.Lock())

    def form_for(self, row):
        return self.forms.get(row["game"])

    def validate(self, form, answers):
        for q in form["questions"]:
            value = answers.get(q["key"], "").strip()
            if q.get("required") and not value:
                raise ValueError(f"Answer {q['label']} before submitting.")
            if len(value) > q.get("max_length", 100):
                raise ValueError(f"{q['label']} is too long.")
            if value and q.get("options") and value not in q["options"]:
                raise ValueError(f"Choose a valid option for {q['label']}.")
        if answers.get("preferred_role_1") == answers.get("preferred_role_2"):
            raise ValueError("Choose two different preferred roles.")

    async def ticket(self, member):
        """Caller holds member lock. Never create a second ticket for a member."""
        row = self.store.member(member.id)
        channel = member.guild.get_channel(row["channel_id"]) if row["channel_id"] else None
        # Recover a channel created immediately before a crash (before SQLite save).
        if channel is None:
            channel = discord.utils.get(member.guild.text_channels, topic=f"rionnag:member:{member.id}")
        if channel is None:
            category = member.guild.get_channel(config.APPLICATIONS_CATEGORY_ID)
            if category is None:
                raise ValueError("Applications category is missing.")
            channel = await member.guild.create_text_channel(
                f"application-{member.id}",
                category=category,
                topic=f"rionnag:member:{member.id}",
                overwrites=ticket_overwrites(member),
                reason="Private onboarding or visitor tryout",
            )
        self.store.update(member.id, channel_id=channel.id)
        form = self.form_for(row) if row["status"] == "pending" else None
        await channel.edit(overwrites=ticket_overwrites(member, form))
        return channel

    async def welcome(self, member):
        from rionnag.ui.onboarding import WelcomeView

        channel = await self.ticket(member)
        row = self.store.member(member.id)
        if row["message_id"]:
            try:
                message = await channel.fetch_message(row["message_id"])
                await message.edit(content=self.welcome_text(row), embed=None, view=WelcomeView(self))
                return
            except discord.NotFound:
                pass
        message = await channel.send(self.welcome_text(row), view=WelcomeView(self))
        self.store.update(member.id, message_id=message.id)

    def welcome_text(self, row):
        text = (
            "Welcome to Rionnag. Choose Visitor or apply for a game tryout. Finish here to unlock the server."
        )
        if row["restore_roles"] is not None:
            text = (
                "Complete the updated game form below. Any saved answers are prefilled and editable. "
                "Complete every new question to restore your previous roles automatically."
            )
        return text

    async def start(self, interaction, game):
        from rionnag.ui.forms import FormPage

        member = await interaction.guild.fetch_member(interaction.user.id)
        row = self.store.member(member.id)
        if row["status"] in {"pending", "deciding"}:
            raise ValueError("Your application is already submitted.")
        own_ticket = row["channel_id"] == interaction.channel_id
        if row["status"] in COMPLETE and not visitor_eligible(member, self.forms):
            raise ValueError("Only visitors can start a new tryout application.")
        if row["status"] not in COMPLETE and not own_ticket:
            raise ValueError("Finish onboarding in your private application channel.")
        if row["restore_roles"] is not None and row["game"] != game:
            raise ValueError("Complete the updated form for your previous game to restore your roles.")
        answers = row["answers"] if row["game"] == game else {}
        self.store.update(member.id, game=game, answers=answers)
        await interaction.response.send_modal(FormPage(self, member.id, game, 0, answers))

    async def choose_visitor(self, interaction):
        member = await interaction.guild.fetch_member(interaction.user.id)
        async with self.lock(member.id):
            row = self.store.member(member.id)
            if row["channel_id"] != interaction.channel_id or row["status"] not in {"new", "reset"}:
                raise ValueError("Choose Visitor only in your unfinished onboarding channel.")
            if row["restore_roles"] is not None:
                raise ValueError("Complete your updated game form to restore your saved roles.")
            visitor = member.guild.get_role(config.VISITOR_ROLE_ID)
            await member.add_roles(visitor, reason="Visitor onboarding completed")
            self.store.update(member.id, status="visitor", answers={"membership": "visitor"})
            await self.close_ticket(member)

    async def submit(self, interaction, member_id, game, answers, version):
        if interaction.user.id != member_id or interaction.guild_id != config.GUILD_ID:
            raise ValueError("This form belongs to another member.")
        async with self.lock(member_id):
            member = await interaction.guild.fetch_member(member_id)
            row = self.store.member(member_id)
            form = self.forms[game]
            if version != form["version"] or row["game"] != game:
                raise ValueError("The form changed while you were filling it out. Open it again.")
            if row["status"] not in {"new", "reset", "visitor", "rejected"}:
                raise ValueError("This application has already been submitted.")
            if row["status"] in {"visitor", "rejected"} and not visitor_eligible(member, self.forms):
                raise ValueError("Only visitors can submit new tryout applications.")
            self.validate(form, answers)
            # Prevent two Discord members from claiming the same public game account.
            from rionnag.integrations.accounts import verify_account

            async with self.account_lock:
                identity = await verify_account(game, answers["username"])
                for other_id in self.store.ids():
                    if other_id == member_id:
                        continue
                    other = self.store.member(other_id)
                    if other["game"] == game and other["answers"].get("player_uid") == identity["uid"]:
                        raise ValueError(
                            "That game account is already linked to another member. Contact the owner."
                        )
                answers = dict(answers, player_uid=identity["uid"], username=identity["name"])
                self.store.update(member_id, answers=answers)
            channel = await self.ticket(member)
            if row["message_id"]:
                try:
                    await (await channel.fetch_message(row["message_id"])).edit(view=None)
                except discord.NotFound:
                    pass
            if row["restore_roles"] is not None:
                # Restoration never invokes manager review, including a former manager's own application.
                await self.restore_member(member, form, answers)
                return
            if member.id == member.guild.owner_id:
                # The owner must complete the form too, but never needs to review their own application.
                self.store.update(
                    member_id, status="deciding", version=form["version"], restore_status="accepted"
                )
                await self.finish_decision(member, automatic=True)
                return
            self.store.update(member_id, status="pending", version=form["version"], message_id=None)
            await self.publish(member, channel)

    async def publish(self, member, channel):
        from rionnag.ui.onboarding import ReviewView

        row = self.store.member(member.id)
        form = self.forms[row["game"]]
        await channel.edit(overwrites=ticket_overwrites(member, form))
        embed = discord.Embed(title=f"{form['name']} tryout application", color=config.COLOR)
        embed.description = f"Applicant: {member.mention}"
        transcript = []
        for q in form["questions"]:
            value = row["answers"].get(q["key"]) or "Not provided"
            transcript.append(f"{q['label']}\n{value}\n")
            escaped = discord.utils.escape_markdown(value)[:1024]
            if len(embed.fields) < 24 and len(embed) + len(q["label"]) + len(escaped) < 5500:
                embed.add_field(name=q["label"], value=escaped, inline=False)
        if len(embed.fields) < len(form["questions"]):
            embed.add_field(
                name="Full application",
                value="Every answer is included in the attached application.txt.",
                inline=False,
            )
        manager = member.guild.get_role(form["manager_role"])
        if row["message_id"]:
            try:
                await (await channel.fetch_message(row["message_id"])).edit(
                    embed=embed, view=ReviewView(self)
                )
                return
            except discord.NotFound:
                pass
        message = await channel.send(
            content=f"{manager.mention} Application from {member.mention}",
            embed=embed,
            view=ReviewView(self),
            allowed_mentions=discord.AllowedMentions(roles=[manager], users=False),
            file=discord.File(io.BytesIO("\n".join(transcript).encode("utf-8")), filename="application.txt"),
        )
        self.store.update(member.id, message_id=message.id)

    async def decide(self, interaction, accepted):
        member_id = next(
            (
                mid
                for mid in self.store.ids()
                if self.store.member(mid)["channel_id"] == interaction.channel_id
            ),
            None,
        )
        if member_id is None:
            raise ValueError("This ticket is no longer active.")
        async with self.lock(member_id):
            row = self.store.member(member_id)
            form = self.form_for(row)
            reviewer = await interaction.guild.fetch_member(interaction.user.id)
            is_owner = reviewer.id == interaction.guild.owner_id
            if not form or (
                not is_owner and (not has_role(reviewer, form["manager_role"]) or reviewer.id == member_id)
            ):
                raise ValueError(
                    "Only the owner or selected game manager can review this application. "
                    "Managers cannot review themselves."
                )
            if row["status"] != "pending":
                raise ValueError("This application has already been decided.")
            member = await interaction.guild.fetch_member(member_id)
            # Record an outbox state BEFORE side effects. Startup retries interrupted decisions.
            status = "accepted" if accepted else "rejected"
            self.store.update(member_id, status="deciding", restore_status=status)
            await self.finish_decision(member)

    async def finish_decision(self, member, automatic=False):
        row = self.store.member(member.id)
        form = self.form_for(row)
        accepted = row["restore_status"] == "accepted"
        visitor = member.guild.get_role(config.VISITOR_ROLE_ID)
        tryout = member.guild.get_role(form["tryout_role"])
        if visitor is None or tryout is None:
            raise ValueError("A configured membership role is missing.")
        if accepted:
            await member.remove_roles(visitor, reason="Tryout accepted")
            await member.add_roles(tryout, reason="Tryout accepted")
            self.store.save_profile(member.guild.id, member.id, form, row["answers"])
        else:
            game_roles = [
                member.guild.get_role(form[k]) for k in ("team_role", "tryout_role", "manager_role")
            ]
            await member.remove_roles(
                *(role for role in game_roles if role in member.roles), reason="Tryout rejected"
            )
            await member.add_roles(visitor, reason="Tryout rejected")
            self.store.remove_profile(member.guild.id, member.id)
        status = "accepted" if accepted else "rejected"
        self.store.update(
            member.id,
            status=status,
            restore_status=None,
            membership_roles=[tryout.id] if accepted else [visitor.id],
            dm_pending=(
                f"Your {form['name']} tryout application was {status}"
                + (" automatically." if automatic else ".")
            ),
        )
        await self.notify(member)
        await self.close_ticket(member)

    async def restore_member(self, member, form, answers):
        row = self.store.member(member.id)
        roles = [member.guild.get_role(role_id) for role_id in row["restore_roles"]]
        if any(role is None for role in roles):
            raise ValueError(
                "A saved role was deleted. The owner must repair the role configuration before restoration."
            )
        if roles:
            await member.add_roles(*roles, reason="Updated game form completed: restore saved membership")
        self.store.save_profile(member.guild.id, member.id, form, answers)
        self.store.update(
            member.id,
            status=row["restore_status"] or "accepted",
            version=form["version"],
            restore_roles=None,
            restore_status=None,
            reset_target=None,
            membership_roles=row["restore_roles"],
            dm_pending=(
                f"Your updated {form['name']} application was accepted automatically. "
                "Your roles are restored."
            ),
        )
        await self.notify(member)
        await self.close_ticket(member)

    async def notify(self, member):
        row = self.store.member(member.id)
        if not row["dm_pending"]:
            return
        try:
            await member.send(row["dm_pending"])
        except discord.Forbidden:
            log.warning("Decision DM blocked for %s; keeping notification for retry", member.id)
            channel = member.guild.get_channel(row["channel_id"])
            if channel:
                await channel.send(
                    row["dm_pending"] + " I could not deliver your DM because DMs are disabled."
                )
        else:
            self.store.update(member.id, dm_pending=None)

    async def close_ticket(self, member):
        row = self.store.member(member.id)
        channel = member.guild.get_channel(row["channel_id"]) if row["channel_id"] else None
        if channel:
            await channel.delete(reason="Onboarding/application completed")
        self.store.update(member.id, channel_id=None, message_id=None)
