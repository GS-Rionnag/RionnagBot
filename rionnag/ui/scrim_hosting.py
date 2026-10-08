"""Persistent public entry points and private, paginated hosting controls."""

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import discord

from rionnag import config
from rionnag.services.scrim_availability import ZONES


def day_groups(slots, zone):
    """Distinct day counts across qualifying sessions, never sum overlapping slots."""
    groups = {}
    for slot in slots:
        day = datetime.fromtimestamp(slot.start, zone).strftime("%Y-%m-%d")
        groups.setdefault(day, []).append(slot)

    def order(item):
        day, candidates = item
        confirmed = set().union(*(s.confirmed for s in candidates))
        available = set().union(*(s.available for s in candidates))
        return (-len(confirmed), -len(available), min(s.secondary for s in candidates), day)

    return dict(sorted(groups.items(), key=order))


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
        view = DayPicker(self.service, interaction.user.id, slots, mode)
        await interaction.followup.send(embed=view.embed(), view=view, ephemeral=True)

    @discord.ui.button(label="Choose a day", style=discord.ButtonStyle.success, custom_id="hosting:choose")
    async def choose(self, interaction, button):
        await self.open(interaction, "add")

    @discord.ui.button(label="My selections", style=discord.ButtonStyle.secondary, custom_id="hosting:mine")
    async def mine(self, interaction, button):
        await self.open(interaction, "remove")

    @discord.ui.button(label="View lineups", style=discord.ButtonStyle.secondary, custom_id="hosting:lineups")
    async def lineups(self, interaction, button):
        await self.open(interaction, "lineup")


class DayPicker(discord.ui.View):
    def __init__(self, service, owner, slots, mode, zone=None):
        super().__init__(timeout=600)
        self.service, self.owner, self.mode = service, owner, mode
        if zone is None:
            player = next((p for p in service.players() if p["member_id"] == owner), None)
            zone = ZONES.get(player["answers"].get("time_zone")) if player else None
        self.zone = ZoneInfo(zone or "America/New_York")
        self.groups = day_groups(slots, self.zone)
        options = []
        for day, candidates in self.groups.items():
            confirmed, available = self.counts(candidates)
            options.append(
                discord.SelectOption(
                    label=datetime.strptime(day, "%Y-%m-%d").strftime("%A, %B %d"),
                    value=day,
                    description=f"{confirmed} confirmed · {available} available",
                )
            )
        select = discord.ui.Select(placeholder="Choose your best day", options=options)
        select.callback = self.selected
        self.add_item(select)

    @staticmethod
    def counts(slots):
        return (
            len(set().union(*(s.confirmed for s in slots))),
            len(set().union(*(s.available for s in slots))),
        )

    async def interaction_check(self, interaction):
        if interaction.user.id == self.owner:
            return True
        await interaction.response.send_message("Open your own day selector from the board.", ephemeral=True)
        return False

    def embed(self):
        embed = discord.Embed(
            title="Choose a scrim day",
            color=config.COLOR,
            description=f"Days use your saved time zone: {self.zone.key}.\n"
            "Most confirmed players first, then most available, then role fit.\n"
            "Counts are distinct players across qualifying times that day. "
            "Choose a day to see exact time-by-time counts.",
        )
        for day, slots in self.groups.items():
            confirmed, available = self.counts(slots)
            embed.add_field(
                name=datetime.strptime(day, "%Y-%m-%d").strftime("%A, %B %d"),
                value=f"**{confirmed} confirmed** · **{available} available**",
                inline=False,
            )
        return embed

    async def selected(self, interaction):
        day = interaction.data["values"][0]
        # Refresh counts and eligibility when advancing, before showing time choices.
        slots = self.service.snapshot()
        if self.mode in {"add", "remove"}:
            slots = [s for s in slots if self.owner in (s.available if self.mode == "add" else s.confirmed)]
        if self.mode == "publish":
            if not self.service.manager(interaction):
                await interaction.response.send_message(
                    "Only managers or the owner can host.", ephemeral=True
                )
                return
            slots = [s for s in slots if s.ready]
        slots = day_groups(slots, self.zone).get(day, [])
        if not slots:
            await interaction.response.send_message(
                "This day no longer has matching sessions. Reopen Choose a day.", ephemeral=True
            )
            return
        view = SlotPicker(
            self.service, self.owner, sorted(slots, key=lambda s: s.order), self.mode, self.zone.key
        )
        await interaction.response.edit_message(embed=view.embed(), view=view)


class SlotPicker(discord.ui.View):
    PAGE = 10

    def __init__(self, service, owner, slots, mode, zone="America/New_York"):
        super().__init__(timeout=600)
        self.service, self.owner, self.slots, self.mode = service, owner, slots, mode
        self.zone = ZoneInfo(zone)
        self.page = 0
        self.day = "all"
        self.duration = service.store.host_settings(service.channel_id)["duration"]
        self.build()

    def filtered(self):
        if self.day == "all":
            return self.slots
        return [s for s in self.slots if self.date_key(s) == self.day]

    def date_key(self, slot):
        return datetime.fromtimestamp(slot.start, self.zone).strftime("%Y-%m-%d")

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
            local = datetime.fromtimestamp(slot.start, self.zone)
            end = datetime.fromtimestamp(slot.end, self.zone)
            options.append(
                discord.SelectOption(
                    label=f"{'✓ ' if self.owner in slot.confirmed else ''}"
                    f"{index}. {local:%I:%M %p} – {end:%I:%M %p %Z}",
                    value=str(slot.start),
                    description=f"{len(slot.confirmed)} confirmed · {len(slot.available)} available · "
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
        for label, delta in (("Previous", -1), ("Next", 1)):
            button = discord.ui.Button(
                label=label, row=1, disabled=not 0 <= self.page + delta < (len(slots) + 9) // 10
            )

            async def turn(interaction, delta=delta):
                self.page += delta
                self.build()
                await interaction.response.edit_message(embed=self.embed(), view=self)

            button.callback = turn
            self.add_item(button)
        back = discord.ui.Button(label="Back to days", row=1)

        async def back_to_days(interaction):
            slots = self.service.snapshot()
            if self.mode in {"add", "remove"}:
                slots = [
                    s for s in slots if self.owner in (s.available if self.mode == "add" else s.confirmed)
                ]
            if self.mode == "publish":
                slots = [s for s in slots if s.ready]
            if not slots:
                await interaction.response.send_message(
                    "No matching sessions remain. Reopen the board.", ephemeral=True
                )
                return
            view = DayPicker(self.service, self.owner, slots, self.mode, self.zone.key)
            await interaction.response.edit_message(embed=view.embed(), view=view)

        back.callback = back_to_days
        self.add_item(back)

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
            description=f"Times below display locally. Dropdown labels use {self.zone.key}.\n"
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
        embed.set_footer(
            text=f"Page {self.page + 1}/{(len(slots) + 9) // 10} · Confirmed, available, then best roles"
        )
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
            await interaction.followup.send(
                text[:2000], ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
            )
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)


class HostApproval(discord.ui.View):
    def __init__(self, service, start, generation):
        super().__init__(timeout=None)
        self.service, self.start, self.generation = service, start, generation
        for label, send, style in (
            ("Yes, send advert", True, discord.ButtonStyle.success),
            ("No", False, discord.ButtonStyle.secondary),
        ):
            button = discord.ui.Button(
                label=label, style=style, custom_id=f"hosting-approval:{start}:{generation}:{int(send)}"
            )

            async def decide(interaction, send=send):
                await interaction.response.defer(thinking=True)
                try:
                    await self.service.decide_notice(interaction, self.start, self.generation, send)
                except ValueError as exc:
                    await interaction.followup.send(str(exc), ephemeral=True)
                    return
                for item in self.children:
                    item.disabled = True
                await interaction.message.edit(view=self)
                await interaction.followup.send(
                    "Advert queued. Delivery status will appear on the scrim board."
                    if send
                    else "Declined. No advert will be sent for this request.",
                    allowed_mentions=discord.AllowedMentions.none(),
                )

            button.callback = decide
            self.add_item(button)
