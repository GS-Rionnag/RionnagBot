"""Daily finder board and private scrim commitment controls."""

import time
from urllib.parse import urlsplit

import discord

from rionnag import config
from rionnag.scrims.scrim_offer_rules import timestamp

BOARD_MARKER = "Rionnag daily scrim opportunities"


def matched_offer_embed(offer, members, key, votes=(), now=None, scrim_count=1, players=(),
                        official=False):
    start = timestamp(offer["Start_Time_timestamp"])
    end = timestamp(offer.get("End_Time_timestamp"))
    now = time.time() if now is None else now
    embed = discord.Embed(
        title=f"{scrim_count} Scrims Found!" if scrim_count > 1 else "Scrim Found!",
        url=offer.get("messageURL"), color=config.COLOR,
        description=(f"Upcoming · Starts <t:{start}:R>" if now < start else "In progress")
        + f"\n<t:{start}:F>"
        + ("\nEnd time not advertised" if end is None else f" – <t:{end}:t>")
        + ("\n**Officially confirmed**" if official else ""),
    )
    ranks = list(dict.fromkeys(offer.get(k) for k in ("rank_minimum", "rank_maximum") if offer.get(k)))
    embed.add_field(name="Opponent rank", value=" to ".join(ranks) or "Not specified")
    by_id = {p["member_id"]: p for p in players}
    lines = []
    for role in ("Tank", "DPS", "Support"):
        names = [f"<@{mid}>" for mid in votes if mid in by_id
                 and by_id[mid]["answers"].get("preferred_role_1") == role]
        if names:
            lines.append(f"{role}: " + ", ".join(names))
    embed.add_field(name=f"Confirmed players ({len(votes)}{'/6' if not official else ''})",
                    value="\n".join(lines) or "None yet.", inline=False)
    remaining = sorted(set(members) - set(votes))
    embed.add_field(name=f"Available players ({len(remaining)})",
                    value=" ".join(f"<@{mid}>" for mid in remaining) or "None remaining.", inline=False)
    if offer.get("messageURL"):
        embed.add_field(name="Original message", value=f"[View scrim offer]({offer['messageURL']})",
                        inline=False)
    source_id = urlsplit(offer.get("messageURL") or "").path.rstrip("/").rsplit("/", 1)[-1]
    if source_id.isascii() and source_id.isdigit() and 0 < int(source_id) < 2**64:
        posted = int(discord.utils.snowflake_time(int(source_id)).timestamp())
        embed.add_field(name="Time posted", value=f"<t:{posted}:F> (<t:{posted}:R>)", inline=False)
    embed.set_footer(text=f"Scrim finder • {key}")
    return embed


class OpportunityBoard(discord.ui.View):
    def __init__(self, publisher):
        super().__init__(timeout=None)
        self.publisher = publisher

    @discord.ui.button(label="Choose a scrim time", style=discord.ButtonStyle.primary,
                       custom_id="scrim-finder:choose")
    async def choose(self, interaction, button):
        slots = await self.publisher.current_slots()
        if not slots:
            await interaction.response.send_message("No scrim opportunities remain today.", ephemeral=True)
            return
        await interaction.response.send_message(
            "Choose a time to confirm or withdraw.",
            view=OpportunityPicker(self.publisher, interaction.user.id, slots), ephemeral=True,
        )


class OpportunityPicker(discord.ui.View):
    def __init__(self, publisher, member_id, slots, page=0):
        super().__init__(timeout=300)
        self.publisher, self.member_id, self.slots, self.page = publisher, member_id, slots, page
        chunk = slots[page * 25:(page + 1) * 25]
        selector = discord.ui.Select(
            placeholder="Select a scrim time",
            options=[discord.SelectOption(label=label[:100], value=key) for key, label in chunk],
        )
        selector.callback = self.selected
        self.add_item(selector)
        if len(slots) > 25:
            for label, offset in (("Previous", -1), ("Next", 1)):
                button = discord.ui.Button(label=label, style=discord.ButtonStyle.secondary,
                                           disabled=not 0 <= page + offset < (len(slots) + 24) // 25)
                async def move(interaction, offset=offset):
                    await interaction.response.edit_message(
                        view=OpportunityPicker(publisher, member_id, slots, page + offset))
                button.callback = move
                self.add_item(button)

    async def interaction_check(self, interaction):
        if interaction.user.id == self.member_id:
            return True
        await interaction.response.send_message("Open your own scrim time picker.", ephemeral=True)
        return False

    async def selected(self, interaction):
        key = interaction.data["values"][0]
        detail = await self.publisher.slot_detail(key)
        if detail is None:
            await interaction.response.edit_message(content="That scrim is no longer available.", view=None)
            return
        offer, start, end = detail
        await interaction.response.edit_message(
            content=f"<t:{start}:F> – <t:{end}:t>\n[Original advert]({offer['messageURL']})",
            view=OpportunitySlot(self.publisher, self.member_id, key), embeds=[],
        )


class OpportunitySlot(discord.ui.View):
    def __init__(self, publisher, member_id, key):
        super().__init__(timeout=300)
        self.publisher, self.member_id, self.key = publisher, member_id, key

    async def interaction_check(self, interaction):
        if interaction.user.id == self.member_id:
            return True
        await interaction.response.send_message("Open your own scrim controls.", ephemeral=True)
        return False

    @discord.ui.button(label="Confirm availability", style=discord.ButtonStyle.success)
    async def confirm(self, interaction, button):
        await self.publisher.vote(interaction, self.key, True)

    @discord.ui.button(label="Withdraw", style=discord.ButtonStyle.secondary)
    async def withdraw(self, interaction, button):
        await self.publisher.vote(interaction, self.key, False)


class ScheduleOverride(discord.ui.View):
    def __init__(self, publisher, member_id, key):
        super().__init__(timeout=300)
        self.publisher, self.member_id, self.key = publisher, member_id, key

    async def interaction_check(self, interaction):
        return interaction.user.id == self.member_id

    @discord.ui.button(label="Yes, join this scrim", style=discord.ButtonStyle.success)
    async def confirm(self, interaction, button):
        await self.publisher.vote(interaction, self.key, True, override=True)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        await interaction.response.edit_message(content="Cancelled.", view=None)


class FinderOwnerApproval(discord.ui.View):
    def __init__(self, publisher, start, generation):
        super().__init__(timeout=None)
        self.publisher, self.start, self.generation = publisher, start, generation
        button = discord.ui.Button(label="Confirm scrim", style=discord.ButtonStyle.success,
                                   custom_id=f"scrim-finder:book:{start}:{generation}")
        button.callback = self.confirm
        self.add_item(button)

    async def confirm(self, interaction):
        guild = self.publisher.bot.get_guild(self.publisher.guild_id)
        if guild is None or interaction.user.id != guild.owner_id:
            await interaction.response.send_message("Only the server owner can confirm this scrim.",
                                                    ephemeral=True)
            return
        await interaction.response.send_message(
            "Have you messaged the advert author and agreed to this scrim?",
            view=FinderFinalConfirmation(self.publisher, self.start, self.generation,
                                         interaction.user.id), ephemeral=True,
        )


class FinderFinalConfirmation(discord.ui.View):
    def __init__(self, publisher, start, generation, owner):
        super().__init__(timeout=300)
        self.publisher, self.start, self.generation, self.owner = publisher, start, generation, owner

    async def interaction_check(self, interaction):
        return interaction.user.id == self.owner

    @discord.ui.button(label="Yes, confirm scrim", style=discord.ButtonStyle.success)
    async def confirm(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        try:
            await self.publisher.book(interaction, self.start, self.generation)
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
        else:
            await interaction.followup.send("Scrim officially confirmed.", ephemeral=True)

    @discord.ui.button(label="No", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        await interaction.response.edit_message(content="Confirmation cancelled.", view=None)


class FinderInvite(discord.ui.View):
    def __init__(self, publisher, member_id, start, duration, kind):
        super().__init__(timeout=None)
        self.publisher, self.member_id = publisher, member_id
        self.start, self.duration, self.kind = start, duration, kind
        for label, accept in (("Yes, join scrim", True), ("No", False)):
            button = discord.ui.Button(
                label=label, style=discord.ButtonStyle.success if accept else discord.ButtonStyle.secondary,
                custom_id=f"scrim-finder:invite:{kind}:{start}:{duration}:{int(accept)}")

            async def answer(interaction, accept=accept):
                if interaction.user.id != member_id:
                    await interaction.response.send_message("This invitation is for another player.",
                                                            ephemeral=True)
                    return
                await interaction.response.defer(ephemeral=True)
                try:
                    await publisher.answer_invite(member_id, start, duration, kind, accept)
                except ValueError as exc:
                    await interaction.followup.send(str(exc), ephemeral=True)
                else:
                    await interaction.followup.send(
                        "You are confirmed for this scrim." if accept else "Invitation declined.",
                        ephemeral=True)

            button.callback = answer
            self.add_item(button)
