"""Search by username, then choose the actual account instead of requiring an exact name."""

import asyncio

import discord

from rionnag.integrations.rivals import queued_lookup, search_player_accounts
from rionnag.ui.availability import AvailabilityView
from rionnag.ui.onboarding import SafeView

AVAILABILITY_VIDEO = "Watch this before choosing your available days: https://youtu.be/TQXOmacHAYs"


async def continue_to_availability(interaction, modal):
    username = modal.answers["username"].strip()
    if modal.game != "marvel-rivals" or (username.isascii() and username.isdecimal()):
        if modal.game == "marvel-rivals":
            modal.answers["player_uid"] = username
        view = AvailabilityView(modal)
        await interaction.response.send_message(
            content=AVAILABILITY_VIDEO, embed=view.embed(), view=view, ephemeral=True
        )
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        candidates = await asyncio.to_thread(queued_lookup, search_player_accounts, username)
    except Exception as exc:
        raise ValueError("Account search is temporarily unavailable. Please try again.") from exc
    if not candidates:
        raise ValueError(
            "No matching accounts found. Try normal characters, part of your username, or your UID."
        )
    await interaction.followup.send(
        "Select your Marvel Rivals account from the search results.",
        view=AccountPicker(modal, candidates),
        ephemeral=True,
    )


class AccountPicker(SafeView):
    def __init__(self, modal, candidates, page=0):
        super().__init__(timeout=900)
        self.modal, self.candidates, self.page = modal, candidates, page
        select = discord.ui.Select(
            placeholder="Choose your game account",
            options=[
                discord.SelectOption(
                    label=row["name"][:100], value=str(index), description=f"UID: {row['uid']}"[:100]
                )
                for index, row in enumerate(candidates)
                if page * 25 <= index < (page + 1) * 25
            ],
        )
        select.callback = self.choose
        self.add_item(select)
        for label, delta in (("Previous", -1), ("Next", 1)):
            button = discord.ui.Button(
                label=label, disabled=page == 0 if delta < 0 else (page + 1) * 25 >= len(candidates)
            )

            async def change(interaction, delta=delta):
                await interaction.response.edit_message(view=AccountPicker(modal, candidates, page + delta))

            button.callback = change
            self.add_item(button)

    async def interaction_check(self, interaction):
        if not await super().interaction_check(interaction):
            return False
        if interaction.user.id != self.modal.owner_id:
            raise ValueError("Only the applicant can select their game account.")
        return True

    async def choose(self, interaction):
        row = self.candidates[int(self.children[0].values[0])]
        self.modal.answers.update(username=row["name"], player_uid=str(row["uid"]))
        view = AvailabilityView(self.modal)
        await interaction.response.edit_message(content=AVAILABILITY_VIDEO, embed=view.embed(), view=view)
        self.stop()
