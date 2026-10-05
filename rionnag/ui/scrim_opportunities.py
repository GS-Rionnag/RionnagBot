"""Public scrim summaries expose only matched members and opponent details."""

import discord

from rionnag import config
from rionnag.scrims.scrim_offer_rules import timestamp


def matched_offer_embed(offer, members, key, votes=()):
    start = timestamp(offer["Start_Time_timestamp"])
    end = timestamp(offer.get("End_Time_timestamp"))
    assumed = end is None
    end = end if end is not None else start + 3600
    embed = discord.Embed(
        title=f"Possible scrim · {len(members)} players available",
        url=offer.get("messageURL"), color=config.COLOR,
        description=f"<t:{start}:F> – <t:{end}:t>\n"
        + ("End time not advertised; checked 1 hour of availability.\n" if assumed else "")
        + "Based on saved availability; attendance is not confirmed.",
    )
    ranks = list(dict.fromkeys(offer.get(k) for k in ("rank_minimum", "rank_maximum") if offer.get(k)))
    embed.add_field(name="Opponent rank", value=" to ".join(ranks) or "Not specified")
    mentions = [f"<@{mid}>" for mid in members]
    # Split large rosters without breaking mentions or Discord embed limits.
    chunks = []
    current = ""
    for mention in mentions:
        if len(current) + len(mention) + 2 > 1024:
            chunks.append(current)
            current = ""
        current += (", " if current else "") + mention
    if current:
        chunks.append(current)
    for index, chunk in enumerate(chunks[:4]):
        embed.add_field(name="Available players" if index == 0 else "Available players (continued)",
                        value=chunk, inline=False)
    if len(chunks) > 4:
        embed.add_field(name="Additional players", value="More members are available than fit in this post.")
    embed.add_field(name=f"Voted {len(votes)}/6",
                    value="\n".join(f"<@{mid}>" for mid in votes) or "No votes yet.", inline=False)
    author = str(offer.get("authorID", ""))
    if author.isdigit():
        embed.add_field(name="Posted by", value=f"<@{author}>")
    if offer.get("messageURL"):
        embed.add_field(name="Original message", value=f"[View scrim offer]({offer['messageURL']})",
                        inline=False)
    if offer.get("messageContent"):
        embed.add_field(name="Original post", value=offer["messageContent"][:1024], inline=False)
    embed.set_footer(text=f"Scrim finder • {key}")
    return embed


class OpportunityVotes(discord.ui.View):
    def __init__(self, publisher, key):
        super().__init__(timeout=None)
        self.publisher, self.key = publisher, key
        for label, style, action in (("Vote", discord.ButtonStyle.success, "add"),
                                     ("Remove vote", discord.ButtonStyle.danger, "remove")):
            button = discord.ui.Button(label=label, style=style, custom_id=f"scrim-vote:{key}:{action}")
            button.callback = self.add_vote if action == "add" else self.remove_vote
            self.add_item(button)

    async def add_vote(self, interaction):
        await self.publisher.vote(interaction, self.key, True)

    async def remove_vote(self, interaction):
        await self.publisher.vote(interaction, self.key, False)
