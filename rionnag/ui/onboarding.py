import logging

import discord

from rionnag import config

log = logging.getLogger(__name__)


def entry_embed():
    return discord.Embed(
        title="Apply for a game tryout",
        description="Choose your game below, complete the form, and select your available days. "
        "Your game manager will review your private application.",
        color=config.COLOR,
    )


async def report(interaction, error):
    if not isinstance(error, ValueError):
        log.error("Interaction failed", exc_info=(type(error), error, error.__traceback__))
    text = (
        str(error)
        if isinstance(error, ValueError)
        else "Something failed. Your progress is saved; try again."
    )
    if interaction.response.is_done():
        await interaction.followup.send(text, ephemeral=True)
    else:
        await interaction.response.send_message(text, ephemeral=True)


class SafeView(discord.ui.View):
    async def interaction_check(self, interaction):
        if interaction.guild_id != config.GUILD_ID:
            await interaction.response.send_message("This bot is for Rionnag only.", ephemeral=True)
            return False
        return True

    async def on_error(self, interaction, error, item):
        await report(interaction, error)


class GameButton(discord.ui.Button):
    def __init__(self, service, key):
        self.service, self.key = service, key
        super().__init__(
            label=service.forms[key]["name"],
            style=discord.ButtonStyle.primary,
            custom_id=f"rionnag:game:{key}",
            emoji=discord.PartialEmoji(name="MR", id=1554291577701146634) if key == "marvel-rivals" else None,
        )

    async def callback(self, interaction):
        async with self.service.lock(interaction.user.id):
            await self.service.start(interaction, self.key)


class EntryView(SafeView):
    def __init__(self, service):
        super().__init__(timeout=None)
        for key in service.forms:
            self.add_item(GameButton(service, key))


class WelcomeView(EntryView):
    def __init__(self, service):
        self.service = service
        super().__init__(service)
        button = discord.ui.Button(
            label="Visitor", custom_id="rionnag:visitor", style=discord.ButtonStyle.secondary
        )
        button.callback = self.visitor
        self.add_item(button)

    async def visitor(self, interaction):
        await interaction.response.defer(ephemeral=True)
        await self.service.choose_visitor(interaction)
        await interaction.followup.send("Welcome! You now have visitor access.", ephemeral=True)


class ReviewView(SafeView):
    def __init__(self, service):
        super().__init__(timeout=None)
        self.service = service
        for accepted, label, style in (
            (True, "Accept", discord.ButtonStyle.success),
            (False, "Reject", discord.ButtonStyle.danger),
        ):
            button = discord.ui.Button(label=label, style=style, custom_id=f"rionnag:review:{accepted}")

            async def callback(interaction, accepted=accepted):
                await interaction.response.defer(ephemeral=True)
                await self.service.decide(interaction, accepted)
                await interaction.followup.send(
                    "Application accepted." if accepted else "Application rejected.", ephemeral=True
                )

            button.callback = callback
            self.add_item(button)

        edit = discord.ui.Button(
            label="Edit", style=discord.ButtonStyle.secondary, custom_id="rionnag:application:edit"
        )
        edit.callback = self.edit
        self.add_item(edit)

    async def edit(self, interaction):
        await self.service.edit_application(interaction)
