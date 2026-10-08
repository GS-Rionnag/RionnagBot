"""Persistent public entry points and private, paginated hosting controls."""

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import discord

from rionnag import config


def lineup_text(slot):
    lines = [
        f"<t:{slot.start}:F> – <t:{slot.end}:t>",
        "**Confirmed lineup**" if slot.ready else "**Suggested lineup — confirmations needed**",
    ]
    for role in ("Tank", "DPS", "Support"):
        lines.append(
            f"**{role}** · "
            + " · ".join(f"<@{mid}>" for mid, assigned in slot.lineup.items() if assigned == role)
        )
    lines.append(f"{6 - slot.secondary} best roles · {slot.secondary} secondary roles")
    reserves = slot.confirmed - slot.lineup.keys()
    if reserves:
        lines.append(
            "**Confirmed substitutes** · " + " · ".join(f"<@{mid}>" for mid in sorted(reserves)[:12])
        )
        if len(reserves) > 12:
            lines.append(f"Plus {len(reserves) - 12} more confirmed substitutes.")
    return "\n".join(lines)


class HostingBoard(discord.ui.View):
    def __init__(self, service):
        super().__init__(timeout=None)
        self.service = service

    async def open(self, interaction, mode):
        await interaction.response.defer(ephemeral=True)
        slots = self.service.snapshot()
        if mode in {"add", "remove"}:
            if not any(p["member_id"] == interaction.user.id for p in self.service.players()):
                await interaction.followup.send(
                    "Complete your current Marvel Rivals form first.", ephemeral=True
                )
                return
            slots = [
                s for s in slots if interaction.user.id in (s.confirmed if mode == "remove" else s.available)
            ]
        if mode == "publish":
            if not self.service.manager(interaction):
                await interaction.followup.send(
                    "Only the owner or Marvel Rivals Managers can host.", ephemeral=True
                )
                return
            slots = [s for s in slots if s.ready]
        if not slots:
            await interaction.followup.send(
                "No sessions match this view yet. Check /edit_form for your schedule.", ephemeral=True
            )
            return
        view = SlotPicker(self.service, interaction.user.id, slots, mode)
        await interaction.followup.send(embed=view.embed(), view=view, ephemeral=True)

    @discord.ui.button(label="Choose times", style=discord.ButtonStyle.success, custom_id="hosting:choose")
    async def choose(self, interaction, button):
        await self.open(interaction, "add")

    @discord.ui.button(label="My selections", style=discord.ButtonStyle.secondary, custom_id="hosting:mine")
    async def mine(self, interaction, button):
        await self.open(interaction, "remove")

    @discord.ui.button(label="View lineups", style=discord.ButtonStyle.secondary, custom_id="hosting:lineups")
    async def lineups(self, interaction, button):
        await self.open(interaction, "lineup")

    @discord.ui.button(label="Host a session", style=discord.ButtonStyle.primary, custom_id="hosting:publish")
    async def publish(self, interaction, button):
        await self.open(interaction, "publish")

    @discord.ui.button(
        label="Host settings", style=discord.ButtonStyle.secondary, custom_id="hosting:settings", row=1
    )
    async def settings(self, interaction, button):
        if not self.service.manager(interaction):
            await interaction.response.send_message(
                "Only the owner or Marvel Rivals Managers can configure hosting.", ephemeral=True
            )
            return
        await interaction.response.send_modal(HostingSettings(self.service))


class SlotPicker(discord.ui.View):
    PAGE = 10

    def __init__(self, service, owner, slots, mode):
        super().__init__(timeout=600)
        self.service, self.owner, self.slots, self.mode = service, owner, slots, mode
        self.page = 0
        self.day = "all"
        self.duration = service.store.host_settings(service.channel_id)["duration"]
        self.build()

    def filtered(self):
        if self.day == "all":
            return self.slots
        return [s for s in self.slots if self.date_key(s) == self.day]

    @staticmethod
    def date_key(slot):
        return datetime.fromtimestamp(slot.start, ZoneInfo("America/New_York")).strftime("%Y-%m-%d")

    async def interaction_check(self, interaction):
        if interaction.user.id == self.owner:
            return True
        await interaction.response.send_message("Open your own controls from the board.", ephemeral=True)
        return False

    def build(self):
        self.clear_items()
        slots = self.filtered()
        section = slots[self.page * self.PAGE : (self.page + 1) * self.PAGE]
        options = []
        for index, slot in enumerate(section, self.page * self.PAGE + 1):
            local = datetime.fromtimestamp(slot.start, ZoneInfo("America/New_York"))
            options.append(
                discord.SelectOption(
                    label=f"{'✓ ' if self.owner in slot.confirmed else ''}"
                    f"{index}. {local:%a %b %d, %I:%M %p %Z}",
                    value=str(slot.start),
                    description=f"{len(slot.confirmed)} confirmed · "
                    f"{6 - slot.secondary} best roles · {'Ready' if slot.ready else 'Needs confirmations'}",
                )
            )
        select = discord.ui.Select(
            placeholder={
                "add": "Select times to confirm",
                "remove": "Select times to withdraw",
                "lineup": "Choose a lineup to view",
                "publish": "Choose a session to preview",
            }[self.mode],
            options=options,
            min_values=1,
            max_values=len(options) if self.mode in {"add", "remove"} else 1,
        )
        select.callback = self.selected
        self.add_item(select)
        dates = sorted({self.date_key(s) for s in self.slots})
        day_select = discord.ui.Select(
            placeholder="Filter by date (Eastern Time)",
            row=1,
            options=[
                discord.SelectOption(
                    label="All dates · best composition first", value="all", default=self.day == "all"
                ),
                *[
                    discord.SelectOption(
                        label=datetime.strptime(day, "%Y-%m-%d").strftime("%A, %B %d"),
                        value=day,
                        default=self.day == day,
                    )
                    for day in dates
                ],
            ],
        )

        async def change_day(interaction):
            self.day = interaction.data["values"][0]
            self.page = 0
            self.build()
            await interaction.response.edit_message(embed=self.embed(), view=self)

        day_select.callback = change_day
        self.add_item(day_select)
        for label, delta in (("Previous", -1), ("Next", 1)):
            button = discord.ui.Button(
                label=label, row=2, disabled=not 0 <= self.page + delta < (len(slots) + 9) // 10
            )

            async def turn(interaction, delta=delta):
                self.page += delta
                self.build()
                await interaction.response.edit_message(embed=self.embed(), view=self)

            button.callback = turn
            self.add_item(button)

    def embed(self):
        title = {
            "add": "Choose every time you can commit to",
            "remove": "Your confirmed times",
            "lineup": "Scrim lineups",
            "publish": "Select a session to host",
        }[self.mode]
        embed = discord.Embed(
            title=title,
            color=config.COLOR,
            description="Times below display in your local time zone. Dropdown labels use Eastern Time.\n"
            "Selections apply immediately. You may confirm multiple sessions and withdraw later.",
        )
        slots = self.filtered()
        for index, slot in enumerate(slots[self.page * 10 : (self.page + 1) * 10], self.page * 10 + 1):
            embed.add_field(
                name=f"{index}. {'Ready' if slot.ready else 'Needs confirmations'}",
                inline=False,
                value=f"<t:{slot.start}:F> – <t:{slot.end}:t> · **{len(slot.confirmed)} confirmed**\n"
                f"{6 - slot.secondary} best / {slot.secondary} secondary · {len(slot.available)} available",
            )
        embed.set_footer(text=f"Page {self.page + 1}/{(len(slots) + 9) // 10} · Best composition first")
        return embed

    async def selected(self, interaction):
        starts = [int(value) for value in interaction.data["values"]]
        await interaction.response.defer(ephemeral=True)
        try:
            if self.mode in {"add", "remove"}:
                await self.service.change_votes(self.owner, starts, self.mode == "add", self.duration)
                await interaction.followup.send(
                    f"{'Confirmed' if self.mode == 'add' else 'Withdrew from'} {len(starts)} session(s). "
                    "Other selections stay saved. Use the board to refresh your list.",
                    ephemeral=True,
                )
                return
            slot = next((s for s in self.service.snapshot() if s.start == starts[0]), None)
            if slot is None:
                raise ValueError("This session is no longer available. Refresh from the board.")
            text = lineup_text(slot)
            advert = self.service.store.host_adverts(self.service.channel_id).get(slot.start)
            if advert:
                frozen = json.loads(advert["lineup"])
                text += "\n**Selected roster**\n" + " · ".join(
                    f"<@{mid}> ({role})" for mid, role in frozen.items()
                )
            players = {p["member_id"]: p for p in self.service.players()}
            secondary = [
                f"<@{mid}> → {role}"
                for mid, role in slot.lineup.items()
                if players[mid]["answers"].get("preferred_role_1") != role
            ]
            if secondary:
                text += "\n**Secondary assignments**\n" + "\n".join(secondary)
            view = None
            if self.mode == "publish":
                if not self.service.manager(interaction) or not slot.ready:
                    raise ValueError("A manager and six confirmed players forming 2–2–2 are required.")
                settings = self.service.store.host_settings(self.service.channel_id)
                if not settings["destination"] or not settings["min_rank"]:
                    raise ValueError(
                        "Configure ranks and an advert destination with /scrim_host_settings first."
                    )
                rank_lines = await self.service.advert_ranks(slot)
                anonymous = "\n".join(
                    f"Player{index} - {current}; {peak} Peak"
                    for index, (current, peak) in enumerate(rank_lines, 1)
                )
                text += (
                    f"\n\n**Post as the owner in <#{settings['destination']}>**\n"
                    f"LFS {settings['min_rank']} - {settings['max_rank']} at <t:{slot.start}:F>\n"
                    f"{anonymous}\n"
                    "Publishing freezes this lineup. You handle opponent conversations."
                )
                view = PublishPreview(
                    self.service,
                    self.owner,
                    slot.start,
                    self.service.preview_token(slot, settings),
                    rank_lines,
                )
                if advert and advert["status"] == "sent":
                    view.children[0].label = "Confirm replacement lineup"
                    text += "\nThis updates the selected roster; the existing advert stays posted."
            options = {"view": view} if view else {}
            await interaction.followup.send(
                text[:2000], ephemeral=True, allowed_mentions=discord.AllowedMentions.none(), **options
            )
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)


class PublishPreview(discord.ui.View):
    def __init__(self, service, owner, start, expected, rank_lines=()):
        super().__init__(timeout=300)
        self.service, self.owner, self.start, self.expected = service, owner, start, expected
        self.rank_lines = rank_lines

    @discord.ui.button(label="Publish LFS advert", style=discord.ButtonStyle.success)
    async def publish(self, interaction, button):
        if interaction.user.id != self.owner:
            await interaction.response.send_message("Open your own advert preview.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            await self.service.publish(interaction, self.start, self.expected, self.rank_lines)
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        button.disabled = True
        await interaction.edit_original_response(view=self)
        await interaction.followup.send(
            "Session saved. The board shows the selected session and advert delivery status.", ephemeral=True
        )


class HostingSettings(discord.ui.Modal, title="Scrim hosting settings"):
    def __init__(self, service):
        super().__init__()
        self.service = service
        settings = service.store.host_settings(service.channel_id)
        self.minimum = discord.ui.TextInput(label="Minimum opponent rank", default=settings["min_rank"] or "")
        self.maximum = discord.ui.TextInput(label="Maximum opponent rank", default=settings["max_rank"] or "")
        self.destination = discord.ui.TextInput(
            label="Advert destination channel ID", default=str(settings["destination"] or "")
        )
        self.duration = discord.ui.TextInput(
            label="Session length in minutes (60–240)", default=str(settings["duration"] // 60)
        )
        for field in (self.minimum, self.maximum, self.destination, self.duration):
            self.add_item(field)

    async def on_submit(self, interaction):
        if not self.service.manager(interaction):
            await interaction.response.send_message(
                "Only game managers or the owner can change these settings.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        try:
            await self.service.configure(
                str(self.minimum), str(self.maximum), str(self.destination), int(str(self.duration))
            )
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        await interaction.followup.send(
            "Hosting settings saved. A duration change clears confirmations "
            "so players can confirm the new session length.",
            ephemeral=True,
        )
