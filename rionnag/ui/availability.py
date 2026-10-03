"""Choose a day, select its time window, repeat, then finish the application."""

import discord

from rionnag import config
from rionnag.ui.onboarding import SafeView

DAYS = ("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")


def hour_label(hour):
    hour %= 24
    return f"{hour % 12 or 12}:00 {'AM' if hour < 12 else 'PM'}"


def validate_days(days):
    if not isinstance(days, dict) or not days:
        raise ValueError("Select at least one day and its available times.")
    for day, window in days.items():
        if day not in DAYS or not isinstance(window, dict):
            raise ValueError("Choose a valid availability day.")
        start, end = window.get("start"), window.get("end")
        if type(start) is not int or type(end) is not int or not 0 <= start < 24 or not 0 <= end <= 24:
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
            description="Select day(s) you are free to play for ATLEAST 1 hour. "
            "This is to find scrims during everyones common hours, "
            "not always a must, can always reject scrims",
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
        view = TimeWindow(self, self.children[0].values[0])
        await interaction.response.edit_message(embed=view.embed(), view=view)

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


class TimeWindow(SafeView):
    """Sequential message selects update end-time options when the start changes."""

    def __init__(self, view, day, start=None, end=None):
        super().__init__(timeout=900)
        self.view, self.day = view, day
        saved = view.days.get(day, {})
        self.start_hour = saved.get("start") if start is None else start
        self.end_hour = saved.get("end") if end is None else end
        if self.start_hour is not None and self.end_hour is not None and self.end_hour <= self.start_hour:
            self.end_hour = None
        self.start = discord.ui.Select(
            placeholder="Available from",
            row=0,
            options=[
                discord.SelectOption(label=hour_label(hour), value=str(hour), default=hour == self.start_hour)
                for hour in range(24)
            ],
        )
        self.start.callback = self.choose_start
        self.add_item(self.start)
        hours = range(self.start_hour + 1, 25) if self.start_hour is not None else []
        self.end = discord.ui.Select(
            placeholder="Available until (at least 1 hour later)",
            row=1,
            disabled=self.start_hour is None,
            options=[
                discord.SelectOption(
                    label=hour_label(hour) + (" (midnight)" if hour == 24 else ""),
                    value=str(hour),
                    default=hour == self.end_hour,
                )
                for hour in hours
            ]
            or [discord.SelectOption(label="Choose a start time first", value="0")],
        )
        self.end.callback = self.choose_end
        self.add_item(self.end)
        save = discord.ui.Button(
            label="Save day",
            style=discord.ButtonStyle.success,
            row=2,
            disabled=self.end_hour is None or self.start_hour is None,
        )
        save.callback = self.save_day
        self.add_item(save)
        back = discord.ui.Button(label="Back to days", row=2)
        back.callback = self.back
        self.add_item(back)

    async def interaction_check(self, interaction):
        return await self.view.interaction_check(interaction)

    def embed(self):
        embed = self.view.embed()
        embed.title = f"{self.day} availability"
        return embed

    async def choose_start(self, interaction):
        view = TimeWindow(self.view, self.day, int(self.start.values[0]), self.end_hour)
        await interaction.response.edit_message(embed=view.embed(), view=view)

    async def choose_end(self, interaction):
        view = TimeWindow(self.view, self.day, self.start_hour, int(self.end.values[0]))
        await interaction.response.edit_message(embed=view.embed(), view=view)

    async def save_day(self, interaction):
        if self.start_hour is None or self.end_hour is None or self.end_hour - self.start_hour < 1:
            raise ValueError("Choose an end time at least one hour after the start.")
        self.view.days[self.day] = {"start": self.start_hour, "end": self.end_hour}
        await self.view.save()
        view = AvailabilityView(self.view.modal)
        await interaction.response.edit_message(embed=view.embed(), view=view)

    async def back(self, interaction):
        await interaction.response.edit_message(embed=self.view.embed(), view=self.view)
