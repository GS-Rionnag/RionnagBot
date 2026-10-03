"""Schema-driven multi-page forms; stable keys preserve answers across versions."""

import discord

from rionnag import config
from rionnag.ui.onboarding import SafeView, report

PAGE_SIZE = 4


def modal_questions(form):
    return [q for q in form["questions"] if q["key"] != "availability"]


class FormPage(discord.ui.Modal):
    def __init__(self, service, owner_id, game, page, answers, editing=False):
        form = service.forms[game]
        super().__init__(title=f"{form['name']} · page {page + 1}"[:45], timeout=600)
        self.service, self.owner_id, self.game, self.page = service, owner_id, game, page
        self.version = form["version"]
        self.editing = editing
        self.answers = dict(answers)
        self.fields = {}
        if page == 0 and game == "marvel-rivals":
            self.add_item(
                discord.ui.TextDisplay(
                    "If your username has special characters, type the normal characters to search for it. "
                    "This is a search, so you do not need the exact username. "
                    "You can also enter your numeric UID."
                )
            )
        for q in modal_questions(form)[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]:
            saved = answers.get(q["key"], "")
            if q.get("options"):
                field = discord.ui.Select(
                    options=[
                        discord.SelectOption(label=value, value=value, default=value == saved)
                        for value in q["options"]
                    ],
                    min_values=1 if q.get("required") else 0,
                    max_values=1,
                    required=q.get("required", False),
                )
            else:
                field = discord.ui.TextInput(
                    default=saved or None,
                    required=q.get("required", False),
                    max_length=q.get("max_length", 100),
                    style=discord.TextStyle.paragraph if q.get("paragraph") else discord.TextStyle.short,
                )
            self.fields[q["key"]] = field
            self.add_item(discord.ui.Label(text=q["label"], component=field))

    async def on_error(self, interaction, error):
        await report(interaction, error)

    async def on_submit(self, interaction):
        if interaction.user.id != self.owner_id or interaction.guild_id != config.GUILD_ID:
            raise ValueError("This form belongs to another member.")
        form = self.service.forms[self.game]
        if self.version != form["version"]:
            raise ValueError("The form changed. Open it again in your ticket.")
        for key, field in self.fields.items():
            self.answers[key] = (
                (field.values[0] if field.values else "")
                if isinstance(field, discord.ui.Select)
                else str(field.value).strip()
            )
        async with self.service.lock(self.owner_id):
            row = self.service.store.member(self.owner_id)
            allowed = {"pending"} if self.editing else {"new", "reset", "visitor", "rejected"}
            if row["status"] not in allowed or row["game"] != self.game:
                raise ValueError("This form is no longer active.")
            if not self.editing:
                self.service.store.update(self.owner_id, answers=self.answers)
        if (self.page + 1) * PAGE_SIZE < len(modal_questions(form)):
            await interaction.response.send_message(
                "Page saved. Continue to the next questions.", view=ContinueView(self), ephemeral=True
            )
        else:
            from rionnag.ui.accounts import continue_to_availability

            await continue_to_availability(interaction, self)


class ContinueView(SafeView):
    def __init__(self, modal):
        super().__init__(timeout=600)
        self.modal = modal
        button = discord.ui.Button(label="Continue", style=discord.ButtonStyle.primary)
        button.callback = self.next_page
        self.add_item(button)

    async def next_page(self, interaction):
        m = self.modal
        if interaction.user.id != m.owner_id:
            raise ValueError("This form belongs to another member.")
        await interaction.response.send_modal(
            FormPage(m.service, m.owner_id, m.game, m.page + 1, m.answers, editing=m.editing)
        )
