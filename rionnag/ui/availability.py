"""Choose a day, select its time window, repeat, then finish the application."""

import discord

from rionnag import config
from rionnag.ui.onboarding import SafeView, report

DAYS = ("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")


def hour_label(hour):
    return f"{hour % 12 or 12}:00 {'AM' if hour < 12 else 'PM'}"


def validate_days(days):
    if not isinstance(days, dict) or not days:
        raise ValueError("Select at least one day and its available times.")
    for day, window in days.items():
        if day not in DAYS or not isinstance(window, dict):
            raise ValueError("Choose a valid availability day.")
        start, end = window.get("start"), window.get("end")
        if type(start) is not int or type(end) is not int or not 0 <= start < 24 or not 0 <= end < 24:
            raise ValueError("Choose valid start and end times.")
        if start == end:
            raise ValueError("Start and end times must be different.")


def schedule_text(days):
    return "\n".join(
        f"{day}: {hour_label(days[day]['start'])} – {hour_label(days[day]['end'])}"
        + (" (next day)" if days[day]["end"] < days[day]["start"] else "")
        for day in DAYS
        if day in days
    )


class AvailabilityView(SafeView):
    def __init__(self, modal):
        super().__init__(timeout=900)
        self.modal = modal
        self.days = {day: dict(window) for day, window in modal.answers.get("availability_days", {}).items()}
        day_picker = discord.ui.Select(
            placeholder="Select a day to add or edit",
            row=0,
            options=[discord.SelectOption(label=day, value=day) for day in DAYS],
        )
        day_picker.callback = self.choose_day
        self.add_item(day_picker)
        if self.days:
            remove = discord.ui.Select(
                placeholder="Remove a saved day (optional)",
                row=1,
                options=[discord.SelectOption(label=day, value=day) for day in DAYS if day in self.days],
            )
            remove.callback = self.remove_day
            self.add_item(remove)
        finish = discord.ui.Button(
            label="Finish & submit", style=discord.ButtonStyle.success, row=2, disabled=not self.days
        )
        finish.callback = self.finish
        self.add_item(finish)

    async def interaction_check(self, interaction):
        if not await super().interaction_check(interaction):
            return False
        if interaction.user.id != self.modal.owner_id:
            raise ValueError("This availability picker belongs to another member.")
        return True

    def embed(self):
        embed = discord.Embed(
            title="Scrim availability",
            color=config.COLOR,
            description="Select a day, choose your start and end times, then add more days. "
            "Press **Finish & submit** when your schedule is complete.",
        )
        embed.add_field(
            name="Time zone", value=self.modal.answers.get("time_zone", "Your selected time zone")
        )
        embed.add_field(
            name="Saved days", value=schedule_text(self.days) or "No days selected yet.", inline=False
        )
        if not self.days and self.modal.answers.get("availability"):
            embed.add_field(
                name="Previous availability", value=self.modal.answers["availability"][:1024], inline=False
            )
        return embed

    async def save(self):
        m = self.modal
        async with m.service.lock(m.owner_id):
            row = m.service.store.member(m.owner_id)
            if (
                m.version != m.service.forms[m.game]["version"]
                or row["game"] != m.game
                or row["status"] not in {"new", "reset", "visitor", "rejected"}
            ):
                raise ValueError("This form is no longer active. Open it again in your ticket.")
            m.answers["availability_days"] = self.days
            m.answers["availability"] = schedule_text(self.days)
            m.service.store.update(m.owner_id, answers=m.answers)

    async def choose_day(self, interaction):
        await interaction.response.send_modal(TimeWindow(self, self.children[0].values[0]))

    async def remove_day(self, interaction):
        day = self.children[1].values[0]
        self.days.pop(day)
        await self.save()
        view = AvailabilityView(self.modal)
        await interaction.response.edit_message(embed=view.embed(), view=view)

    async def finish(self, interaction):
        validate_days(self.days)
        await self.save()
        await interaction.response.defer(ephemeral=True)
        m = self.modal
        await m.service.submit(interaction, m.owner_id, m.game, m.answers, m.version)
        await interaction.edit_original_response(
            content="Application submitted. Your result will be sent by DM.", embed=None, view=None
        )
        self.stop()


class TimeWindow(discord.ui.Modal):
    def __init__(self, view, day):
        super().__init__(title=f"{day} availability", timeout=600)
        self.view, self.day = view, day
        saved = view.days.get(day, {})
        self.start = self.selector(saved.get("start"))
        self.end = self.selector(saved.get("end"))
        self.add_item(discord.ui.Label(text="Available from", component=self.start))
        self.add_item(discord.ui.Label(text="Available until", component=self.end))

    @staticmethod
    def selector(saved):
        return discord.ui.Select(
            options=[
                discord.SelectOption(label=hour_label(hour), value=str(hour), default=hour == saved)
                for hour in range(24)
            ],
            min_values=1,
            max_values=1,
            required=True,
        )

    async def on_error(self, interaction, error):
        await report(interaction, error)

    async def on_submit(self, interaction):
        if interaction.user.id != self.view.modal.owner_id or interaction.guild_id != config.GUILD_ID:
            raise ValueError("This availability picker belongs to another member.")
        window = dict(start=int(self.start.values[0]), end=int(self.end.values[0]))
        validate_days({self.day: window})
        self.view.days[self.day] = window
        await self.view.save()
        view = AvailabilityView(self.view.modal)
        await interaction.response.edit_message(embed=view.embed(), view=view)
