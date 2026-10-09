"""Choose weekly availability blocks, including breaks and overnight blocks."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import discord

from rionnag import config
from rionnag.services.scrim_availability import day_windows, window_hours
from rionnag.ui.onboarding import SafeView

DAYS = ("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")


def hour_label(hour):
    hour %= 24
    return f"{hour % 12 or 12}:00 {'AM' if hour < 12 else 'PM'}"


def validate_days(days):
    if not isinstance(days, dict) or not days:
        raise ValueError("Select at least one day and its available times.")
    for day, blocks in days.items():
        if day not in DAYS or not day_windows(blocks):
            raise ValueError("Choose a valid availability day.")
        previous_end = -1
        for block in sorted(day_windows(blocks), key=lambda item: item.get("start", -1)
                            if isinstance(item, dict) and type(item.get("start")) is int else -1):
            hours = window_hours(block)
            if not hours:
                raise ValueError("Choose valid start and end times at least one hour apart.")
            start, end = hours
            if start < previous_end:
                raise ValueError(f"{day} has overlapping blocks. Edit a block or leave a break.")
            previous_end = end


def selected_zone(time_zone):
    zones = {
        "Eastern Time (ET)": "America/New_York",
        "Central Time (CT)": "America/Chicago",
        "Mountain Time (MT)": "America/Denver",
        "Pacific Time (PT)": "America/Los_Angeles",
    }
    return ZoneInfo(zones.get(time_zone, "America/New_York"))


def schedule_text(days, time_zone="Eastern Time (ET)", now=None, limit=None):
    zone = selected_zone(time_zone)
    today = (now or datetime.now(UTC)).astimezone(zone).date()
    lines = []
    for index, day in enumerate(DAYS):
        if day not in days:
            continue
        date = today + timedelta(days=((index - 1) % 7 - today.weekday()) % 7)
        midnight = datetime(date.year, date.month, date.day, tzinfo=zone)
        for block in day_windows(days[day]):
            hours = window_hours(block)
            if not hours:
                continue
            start_hour, end_hour = hours
            start = midnight + timedelta(hours=start_hour)
            end = midnight + timedelta(hours=end_hour)
            suffix = " (next day)" if end_hour >= 24 else ""
            lines.append(f"{day}: <t:{int(start.timestamp())}:t> – <t:{int(end.timestamp())}:t>{suffix}")
    if limit is None:
        return "\n".join(lines)
    shown = []
    for index, line in enumerate(lines):
        remainder = len(lines) - index - 1
        candidate = "\n".join([*shown, line])
        if remainder:
            candidate += f"\n… and {remainder} more blocks"
        if len(candidate) > limit:
            break
        shown.append(line)
    hidden = len(lines) - len(shown)
    return "\n".join(shown) + (f"\n… and {hidden} more blocks" if hidden else "")


class AvailabilityView(SafeView):
    def __init__(self, modal):
        super().__init__(timeout=900)
        self.modal = modal
        self.submitting = False
        self.submitted = False
        self.days = {
            day: [{"start": hours[0], "end": hours[1]}
                  for block in day_windows(blocks)
                  if (hours := window_hours(block))]
            for day, blocks in modal.answers.get("availability_days", {}).items()
        }
        day_picker = discord.ui.Select(
            placeholder="Choose a day to add a time block",
            row=0,
            options=[discord.SelectOption(label=day, value=day) for day in DAYS],
        )
        day_picker.callback = self.choose_day
        self.add_item(day_picker)
        finish = discord.ui.Button(
            label="Finish & submit", style=discord.ButtonStyle.success, row=1, disabled=not self.days
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
            description="Choose a day, then set a start and end time for each block of at least 1 hour. "
            "Add more blocks to leave breaks. End times can be on the next day; "
            "that day's own blocks stay separate. You can always decline a scrim.",
        )
        embed.add_field(
            name="Time zone", value=self.modal.answers.get("time_zone", "Your selected time zone")
        )
        embed.add_field(
            name="Saved days",
            value=schedule_text(self.days, self.modal.answers.get("time_zone"), limit=1024)
                  or "No days selected yet.",
            inline=False,
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
            edit_status = m.editing if isinstance(m.editing, str) else "pending"
            allowed = {edit_status} if m.editing else {"new", "reset", "visitor", "rejected"}
            if (
                m.version != m.service.forms[m.game]["version"]
                or row["game"] != m.game
                or row["status"] not in allowed
            ):
                raise ValueError("This form is no longer active. Open it again in your ticket.")
            m.answers["availability_days"] = self.days
            m.answers["availability"] = schedule_text(
                self.days, m.answers.get("time_zone"), limit=1000
            )
            if not m.editing:
                m.service.store.update(m.owner_id, answers=m.answers)

    async def choose_day(self, interaction):
        view = DayBlocks(self, self.children[0].values[0])
        await interaction.response.edit_message(embed=view.embed(), view=view)

    async def finish(self, interaction):
        if self.submitting or self.submitted:
            await interaction.response.send_message(
                "Your form is already saving." if self.submitting else "Your form has already been saved.",
                ephemeral=True,
            )
            return
        validate_days(self.days)
        self.submitting = True
        previous_controls = [(item, item.disabled) for item in self.children]
        finish = self.children[-1]
        previous_label = finish.label
        for item in self.children:
            item.disabled = True
        finish.label = "Updating…" if self.modal.editing else "Submitting…"
        try:
            await interaction.response.edit_message(
                content="Updating your form… Please wait."
                if self.modal.editing
                else "Submitting your application… Please wait.",
                embed=None,
                view=self,
            )
            await self.save()
            m = self.modal
            if m.editing:
                await m.service.submit(
                    interaction, m.owner_id, m.game, m.answers, m.version, editing=m.editing
                )
            else:
                await m.service.submit(interaction, m.owner_id, m.game, m.answers, m.version)
        except Exception:
            for item, disabled in previous_controls:
                item.disabled = disabled
            finish.label = previous_label
            await interaction.edit_original_response(content=None, embed=self.embed(), view=self)
            raise
        finally:
            self.submitting = False
        self.submitted = True
        await interaction.edit_original_response(
            content="Your form data has been updated."
            if m.editing
            else "Application submitted. Your result will be sent by DM.",
            embed=None,
            view=None,
        )
        self.stop()


class DayBlocks(SafeView):
    """Keep a day's add and edit actions together, with at most 24 hour blocks."""

    def __init__(self, view, day):
        super().__init__(timeout=900)
        self.view, self.day = view, day
        blocks = view.days.get(day, [])
        add = discord.ui.Button(label="Add time block", style=discord.ButtonStyle.primary, row=0)
        add.callback = self.add_block
        self.add_item(add)
        if blocks:
            picker = discord.ui.Select(
                placeholder="Edit or remove a block",
                row=1,
                options=[discord.SelectOption(
                    label=f"{hour_label(block['start'])} – {hour_label(block['end'])}"
                          + (" next day" if block['end'] >= 24 else ""),
                    value=str(index),
                ) for index, block in enumerate(blocks)],
            )
            picker.callback = self.edit_block
            self.add_item(picker)
        back = discord.ui.Button(label="Back to all days", row=2)
        back.callback = self.back
        self.add_item(back)

    async def interaction_check(self, interaction):
        return await self.view.interaction_check(interaction)

    def embed(self):
        embed = self.view.embed()
        embed.title = f"{self.day} availability"
        return embed

    async def add_block(self, interaction):
        window = TimeWindow(self.view, self.day)
        await interaction.response.edit_message(embed=window.embed(), view=window)

    async def edit_block(self, interaction):
        window = TimeWindow(self.view, self.day, index=int(self.children[1].values[0]))
        await interaction.response.edit_message(embed=window.embed(), view=window)

    async def back(self, interaction):
        await interaction.response.edit_message(embed=self.view.embed(), view=self.view)


class TimeWindow(SafeView):
    """Sequential message selects update end-time options when the start changes."""

    def __init__(self, view, day, start=None, end=None, index=None):
        super().__init__(timeout=900)
        self.view, self.day, self.index = view, day, index
        saved = view.days.get(day, [])[index] if index is not None else {}
        self.start_hour = saved.get("start") if start is None else start
        self.end_hour = saved.get("end") if end is None else end
        if (self.start_hour is not None and self.end_hour is not None
                and not self.start_hour < self.end_hour <= self.start_hour + 24):
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
        hours = range(self.start_hour + 1, self.start_hour + 25) if self.start_hour is not None else []
        self.end = discord.ui.Select(
            placeholder="Available until (at least 1 hour later)",
            row=1,
            disabled=self.start_hour is None,
            options=[
                discord.SelectOption(
                    label=hour_label(hour) + (" (next day)" if hour >= 24 else ""),
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
            label="Save block",
            style=discord.ButtonStyle.success,
            row=2,
            disabled=self.end_hour is None or self.start_hour is None,
        )
        save.callback = self.save_day
        self.add_item(save)
        back = discord.ui.Button(label=f"Back to {day}", row=2)
        back.callback = self.back
        self.add_item(back)
        if index is not None:
            remove = discord.ui.Button(label="Remove block", style=discord.ButtonStyle.danger, row=2)
            remove.callback = self.remove_block
            self.add_item(remove)

    async def interaction_check(self, interaction):
        return await self.view.interaction_check(interaction)

    def embed(self):
        embed = self.view.embed()
        embed.title = f"{self.day} • {'Edit block' if self.index is not None else 'Add block'}"
        embed.description = "Choose when you are free. End times marked next day cross midnight. " \
            "Save more blocks on this day to leave breaks."
        return embed

    async def choose_start(self, interaction):
        view = TimeWindow(self.view, self.day, int(self.start.values[0]), self.end_hour, self.index)
        await interaction.response.edit_message(embed=view.embed(), view=view)

    async def choose_end(self, interaction):
        view = TimeWindow(self.view, self.day, self.start_hour, int(self.end.values[0]), self.index)
        await interaction.response.edit_message(embed=view.embed(), view=view)

    async def save_day(self, interaction):
        if self.start_hour is None or self.end_hour is None or self.end_hour - self.start_hour < 1:
            raise ValueError("Choose an end time at least one hour after the start.")
        blocks = [dict(block) for block in self.view.days.get(self.day, [])]
        if self.index is not None:
            blocks.pop(self.index)
        blocks.append({"start": self.start_hour, "end": self.end_hour})
        blocks.sort(key=lambda block: block["start"])
        candidate = dict(self.view.days, **{self.day: blocks})
        validate_days(candidate)
        await interaction.response.defer()
        self.view.days = candidate
        await self.view.save()
        view = AvailabilityView(self.view.modal)
        await interaction.edit_original_response(embed=view.embed(), view=view)

    async def remove_block(self, interaction):
        await interaction.response.defer()
        blocks = self.view.days[self.day]
        blocks.pop(self.index)
        if not blocks:
            self.view.days.pop(self.day)
        await self.view.save()
        view = AvailabilityView(self.view.modal)
        await interaction.edit_original_response(embed=view.embed(), view=view)

    async def back(self, interaction):
        view = DayBlocks(self.view, self.day)
        await interaction.response.edit_message(embed=view.embed(), view=view)
