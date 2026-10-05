"""Public scrim summaries expose only matched members and opponent details."""

import time

import discord

from rionnag import config
from rionnag.scrims.scrim_offer_rules import timestamp


def matched_offer_embed(offer, members, key, votes=(), now=None):
    start = timestamp(offer["Start_Time_timestamp"])
    end = timestamp(offer.get("End_Time_timestamp"))
    assumed = end is None
    end = end if end is not None else start + 3600
    now = time.time() if now is None else now
    status = f"Upcoming • Starts <t:{start}:R>" if now < start else (
        "In progress" if assumed else f"In progress • Ends <t:{end}:R>"
    )
    embed = discord.Embed(
        title="Scrim Found!",
        url=offer.get("messageURL"), color=config.COLOR,
        description=f"{status}\n<t:{start}:F>"
        + ("\nEnd time not advertised" if assumed else f" – <t:{end}:t>"),
    )
    ranks = list(dict.fromkeys(offer.get(k) for k in ("rank_minimum", "rank_maximum") if offer.get(k)))
    embed.add_field(name="Opponent rank", value=" to ".join(ranks) or "Not specified")
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
