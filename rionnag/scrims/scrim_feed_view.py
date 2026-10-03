"""Manager preview of collected offers; no channels or applications are created."""

from __future__ import annotations

import discord

from rionnag.scrims.scrim_offer_rules import timestamp


def offer_embed(offer: dict, position: int, total: int) -> discord.Embed:
    url = offer.get("messageURL")
    embed = discord.Embed(title="Scrim opportunity", color=discord.Color.from_rgb(125, 0, 255), url=url)
    ranks = list(dict.fromkeys(offer.get(key) for key in ("rank_minimum", "rank_maximum") if offer.get(key)))
    embed.add_field(name="Rank range", value=" to ".join(ranks) or "Not specified")
    start = timestamp(offer.get("Start_Time_timestamp"))
    end = timestamp(offer.get("End_Time_timestamp"))
    embed.add_field(name="Start time", value=f"<t:{start}:t>" if start is not None else "Not specified")
    if end is not None:
        embed.add_field(name="End time", value=f"<t:{end}:t>")
    author_id = str(offer.get("authorID", ""))
    if author_id.isdigit():
        embed.add_field(name="Posted by", value=f"<@{author_id}>", inline=False)
    if url:
        embed.add_field(name="Original message", value=f"[Jump to message]({url})", inline=False)
    content = offer.get("messageContent")
    if content:
        embed.add_field(name="Original post", value=content[:1024], inline=False)
    embed.set_footer(text=f"Offer {position + 1} of {total} • Collected data; availability may have changed")
    return embed


class OfferPreview(discord.ui.View):
    def __init__(self, offers: list[dict], viewer_id: int):
        super().__init__(timeout=900)
        self.offers = sorted(offers, key=lambda o: o.get("messageURL") or "", reverse=True)
        self.viewer_id = viewer_id
        self.position = 0
        self.update_buttons()

    def embed(self):
        return offer_embed(self.offers[self.position], self.position, len(self.offers))

    def update_buttons(self):
        self.previous.disabled = self.position == 0
        self.next_offer.disabled = self.position == len(self.offers) - 1

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.viewer_id:
            return True
        await interaction.response.send_message(
            "Open your own preview with /scrim_opportunities.",
            ephemeral=True,
        )
        return False

    async def move(self, interaction, offset):
        self.position = max(0, min(len(self.offers) - 1, self.position + offset))
        self.update_buttons()
        await interaction.response.edit_message(
            embed=self.embed(), view=self, allowed_mentions=discord.AllowedMentions.none()
        )

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.move(interaction, -1)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next_offer(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.move(interaction, 1)
