"""Persistent public entry points and private, paginated hosting controls."""

import json
import logging
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import discord

from rionnag import config
from rionnag.services.scrim_availability import ZONES

log = logging.getLogger(__name__)


class HostingView(discord.ui.View):
    async def on_error(self, interaction, error, item):
        if not isinstance(error, ValueError):
            log.error("Hosting control failed: %s", type(item).__name__, exc_info=error)
        text = str(error) if isinstance(error, ValueError) else (
            "Could not finish this action. Reopen Choose a day from the board and try again."
        )
        try:
            if interaction.response.is_done():
                await interaction.followup.send(text, ephemeral=True)
            else:
                await interaction.response.send_message(text, ephemeral=True)
        except discord.HTTPException:
            log.warning("Could not deliver hosting error feedback")


class SessionCard(HostingView):
    def __init__(self, service, position, disabled=False):
        super().__init__(timeout=None)
        for label, joining in (("Join scrim", True), ("Withdraw", False)):
            button = discord.ui.Button(
                label=label, custom_id=f"hosting-card:{position}:{int(joining)}", disabled=disabled,
                style=discord.ButtonStyle.success if joining else discord.ButtonStyle.secondary,
            )

            async def clicked(interaction, joining=joining):
                await interaction.response.defer(ephemeral=True, thinking=True)
                match = re.fullmatch(r"<t:(\d+):F>", interaction.message.embeds[0].title or "")
                if not match:
                    raise ValueError("This card no longer has an active session.")
                start = int(match[1])
                end = re.search(r"<t:(\d+):t>", interaction.message.embeds[0].description or "")
                if not end:
                    raise ValueError("This session changed. Refresh the board.")
                duration = int(end[1]) - start
                if not joining:
                    await service.change_votes(interaction.user.id, [start], False)
                    await interaction.followup.send("You withdrew from this scrim.", ephemeral=True)
                elif await service.direct_join(interaction.user.id, start, duration):
                    await interaction.followup.send("You are confirmed for this scrim.", ephemeral=True)
                else:
                    await interaction.followup.send(
                        f"<t:{start}:F> – <t:{start + duration}:t>\n"
                        "This time is not in your saved schedule. Are you sure you want to join?",
                        view=ScheduleOverride(service, interaction.user.id, start, duration), ephemeral=True,
                    )

            button.callback = clicked
            self.add_item(button)


class ScheduleOverride(HostingView):
    def __init__(self, service, owner, start, duration):
        super().__init__(timeout=300)
        self.service, self.owner, self.start, self.duration = service, owner, start, duration

    async def interaction_check(self, interaction):
        return interaction.user.id == self.owner

    @discord.ui.button(label="Yes, join this scrim", style=discord.ButtonStyle.success)
    async def accept(self, interaction, button):
        await interaction.response.defer()
        await self.service.direct_join(self.owner, self.start, self.duration, override=True)
        await interaction.edit_original_response(content="You are confirmed for this scrim.", view=None)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        await interaction.response.edit_message(content="Cancelled. You have not joined.", view=None)
        self.stop()


class ScrimInvite(HostingView):
    def __init__(self, service, owner, start, duration):
        super().__init__(timeout=None)
        for label, accept in (("Yes", True), ("No", False)):
            button = discord.ui.Button(
                label=label, custom_id=f"hosting-invite:{owner}:{start}:{duration}:{int(accept)}",
                style=discord.ButtonStyle.success if accept else discord.ButtonStyle.secondary,
            )

            async def answer(interaction, accept=accept):
                if interaction.user.id != owner:
                    await interaction.response.send_message("This invitation belongs to another player.",
                                                            ephemeral=True)
                    return
                await interaction.response.defer()
                await service.answer_invite(owner, start, duration, accept)
                for item in self.children:
                    item.disabled = True
                await interaction.edit_original_response(
                    content="You're confirmed for this scrim." if accept else "Invitation declined.",
                    view=self,
                )

            button.callback = answer
            self.add_item(button)


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
        return (day, -len(confirmed), -len(available), min(s.secondary for s in candidates))

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


class HostingBoard(HostingView):
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


class DayPicker(HostingView):
    def __init__(self, service, owner, slots, mode, zone=None):
        super().__init__(timeout=600)
        self.service, self.owner, self.mode = service, owner, mode
        if zone is None:
            player = next((p for p in service.players() if p["member_id"] == owner), None)
            zone = ZONES.get(player["answers"].get("time_zone")) if player else None
        self.zone = ZoneInfo(zone or "America/New_York")
        self.groups = day_groups(slots, self.zone)
        if mode == "add":
            self.groups = dict(list(self.groups.items())[:5])
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
        confirmed = set().union(*(s.confirmed for s in slots))
        return (
            len(confirmed),
            len(set().union(*(s.available for s in slots)) - confirmed),
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
            "Soonest days first, then confirmed players, then available players.\n"
            + ("Showing the next five matching days.\n" if self.mode == "add" else "") +
            "Counts are distinct players across qualifying times that day. "
            "Choose a day to see exact time-by-time counts.",
        )
        for day, slots in self.groups.items():
            confirmed_ids = set().union(*(s.confirmed for s in slots))
            available_ids = set().union(*(s.available for s in slots)) - confirmed_ids
            mentions = " ".join(f"<@{mid}>" for mid in sorted(available_ids)) or "None remaining."
            embed.add_field(
                name=datetime.strptime(day, "%Y-%m-%d").strftime("%A, %B %d"),
                value=(f"**{len(confirmed_ids)} confirmed**\n"
                       f"**Available players** ({len(available_ids)})\n{mentions}")[:1024],
                inline=False,
            )
        return embed

    async def selected(self, interaction):
        await interaction.response.defer()
        day = interaction.data["values"][0]
        # Refresh counts and eligibility when advancing, before showing time choices.
        slots = self.service.snapshot()
        if self.mode in {"add", "remove"}:
            slots = [s for s in slots if self.owner in (s.available if self.mode == "add" else s.confirmed)]
        if self.mode == "publish":
            if not self.service.manager(interaction):
                await interaction.followup.send(
                    "Only managers or the owner can host.", ephemeral=True
                )
                return
            slots = [s for s in slots if s.ready]
        slots = day_groups(slots, self.zone).get(day, [])
        if not slots:
            await interaction.followup.send(
                "This day no longer has matching sessions. Reopen Choose a day.", ephemeral=True
            )
            return
        view = SlotPicker(
            self.service, self.owner, sorted(slots, key=lambda s: s.order), self.mode, self.zone.key
        )
        await interaction.edit_original_response(embed=view.embed(), view=view)


class SlotPicker(HostingView):
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
                    default=self.mode == "add" and self.owner in slot.confirmed,
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
            min_values=0 if self.mode == "add" else 1,
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
            await interaction.response.defer()
            slots = self.service.snapshot()
            if self.mode in {"add", "remove"}:
                slots = [
                    s for s in slots if self.owner in (s.available if self.mode == "add" else s.confirmed)
                ]
            if self.mode == "publish":
                slots = [s for s in slots if s.ready]
            if not slots:
                await interaction.followup.send(
                    "No matching sessions remain. Reopen the board.", ephemeral=True
                )
                return
            view = DayPicker(self.service, self.owner, slots, self.mode, self.zone.key)
            await interaction.edit_original_response(embed=view.embed(), view=view)

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
            "Selections apply immediately. Check times to confirm; uncheck them to withdraw. "
            "Other pages stay saved." if self.mode == "add" else
            f"Times below display locally. Dropdown labels use {self.zone.key}.\n"
            "Select the confirmed times you want to withdraw from.",
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
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            if self.mode in {"add", "remove"}:
                if self.mode == "add":
                    page = self.filtered()[self.page * self.PAGE : (self.page + 1) * self.PAGE]
                    withdrawn = [s.start for s in page if self.owner in s.confirmed and s.start not in starts]
                    await self.service.change_votes(
                        self.owner, starts, True, self.duration, withdraw_starts=withdrawn
                    )
                    for slot in page:
                        if slot.start in starts:
                            slot.confirmed.add(self.owner)
                        else:
                            slot.confirmed.discard(self.owner)
                    self.build()
                    await interaction.followup.edit_message(
                        interaction.message.id, embed=self.embed(), view=self
                    )
                else:
                    await self.service.change_votes(self.owner, starts, False, self.duration)
                    await interaction.followup.edit_message(
                        interaction.message.id,
                        content="Selected confirmations withdrawn.", embed=None, view=None
                    )
                    self.stop()
                action = "Saved selections for" if self.mode == "add" else "Withdrew from"
                await interaction.followup.send(
                    f"{action} {len(starts)} session(s). "
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


class AdvertRanks(discord.ui.Modal, title="Scrim advert ranks"):
    minimum = discord.ui.TextInput(label="Minimum rank", default="Grandmaster", max_length=30)
    maximum = discord.ui.TextInput(label="Maximum rank", default="Celestial", max_length=30)

    def __init__(self, service, start, generation):
        super().__init__()
        self.service, self.start, self.generation = service, start, generation

    async def on_submit(self, interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await self.service.decide_notice(
                interaction, self.start, self.generation, True,
                f"{self.minimum.value} - {self.maximum.value}",
            )
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        await interaction.followup.send("Advert queued with your rank range.", ephemeral=True)

    async def on_error(self, interaction, error):
        await HostingView().on_error(interaction, error, self)


class HostApproval(HostingView):
    def __init__(self, service, start, generation):
        super().__init__(timeout=None)
        self.service, self.start, self.generation = service, start, generation
        advert = service.store.host_adverts(service.channel_id).get(start)
        send_controls = [] if advert and advert["status"] == "sent" else [
            ("Enter ranks & send", True, discord.ButtonStyle.success),
        ]
        for label, send, style in send_controls:
            button = discord.ui.Button(
                label=label, style=style, custom_id=f"hosting-approval:{start}:{generation}:{int(send)}"
            )

            async def decide(interaction, send=send):
                if send:
                    guild = self.service.bot.get_guild(config.GUILD_ID)
                    if not guild or interaction.user.id != guild.owner_id:
                        await interaction.response.send_message("Only the server owner can set advert ranks.",
                                                                ephemeral=True)
                        return
                    await interaction.response.send_modal(
                        AdvertRanks(self.service, self.start, self.generation)
                    )
                    return
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
        for label, bump in (("Bump post", True), ("Confirm scrim", False)):
            button = discord.ui.Button(
                label=label, row=1, custom_id=f"hosting-manage:{start}:{generation}:{int(bump)}",
                style=discord.ButtonStyle.primary if bump else discord.ButtonStyle.success,
            )

            async def manage(interaction, bump=bump):
                await interaction.response.defer(thinking=True)
                try:
                    await service.manage_session(interaction, start, generation, bump)
                except ValueError as exc:
                    await interaction.followup.send(str(exc), ephemeral=True)
                    return
                if not bump:
                    slot = next((s for s in service.snapshot() if s.start == start), None)
                    if slot:
                        await interaction.message.edit(embed=service.owner_panel(slot, generation))
                await interaction.followup.send(
                    "Bump queued. The old advert will be deleted and reposted."
                    if bump else "Scrim officially confirmed. The main board is updated."
                )

            button.callback = manage
            self.add_item(button)
