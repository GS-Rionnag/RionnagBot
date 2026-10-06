"""Adapt the existing scrim subsystem to the new application/profile store."""

import logging

import discord
from discord import app_commands
from discord.ext import commands, tasks

from rionnag import config
from rionnag.scrims.scrim_discord import ScrimController
from rionnag.scrims.scrim_feed import FeedStore
from rionnag.scrims.scrim_feed_view import OfferPreview
from rionnag.services.permissions import has_role
from rionnag.services.scrim_opportunities import select_offers
from rionnag.services.scrim_search import normalize_filter, rank_matches, rank_suggestions


class Scrims(commands.Cog):
    def __init__(self, bot, service, publisher=None):
        self.bot, self.service = bot, service
        self.publisher = publisher
        self.controller = ScrimController(
            bot,
            service.store.connection,
            service.store.saved_profile,
            self.authorize,
            tuple(f["name"] for f in service.forms.values()),
            discord.Color(config.COLOR),
            account_uid=service.store.saved_uid,
        )
        self.restored = False

    async def cog_load(self):
        if self.publisher:
            self.publisher.register_views()
            self.publish_opportunities.start()

    async def cog_unload(self):
        self.publish_opportunities.cancel()

    @tasks.loop(seconds=60)
    async def publish_opportunities(self):
        try:
            # Forms can be replaced when the owner reloads definitions.
            self.publisher.forms = self.service.forms
            await self.publisher.sync()
        except Exception:
            logging.getLogger(__name__).exception("Scrim opportunity refresh failed; will retry")

    @publish_opportunities.before_loop
    async def before_opportunity_refresh(self):
        await self.bot.wait_until_ready()

    def authorize(self, interaction, game):
        form = next((f for f in self.service.forms.values() if f["name"] == game), None)
        return bool(
            form
            and interaction.guild_id == config.GUILD_ID
            and has_role(interaction.user, form["manager_role"])
        )

    @commands.Cog.listener()
    async def on_message(self, message):
        if (self.publisher and message.channel.id == self.publisher.channel_id
                and not (message.author.id == self.bot.user.id and any(
                    embed.footer.text == "Scrim finder vote summary" for embed in message.embeds))):
            async with self.publisher.lock:
                await self.publisher.sync_vote_summary(message.channel)

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.restored:
            await self.controller.restore()
            self.restored = True

    @app_commands.command(
        name="scrim_rank", description="Set the default opponent rank search for scrim offers"
    )
    @app_commands.describe(min_rank="Minimum rank, or Any to clear the filter",
                           max_rank="Maximum rank; omit to search only the minimum rank")
    async def rank_filter(self, interaction: discord.Interaction, min_rank: str, max_rank: str | None = None):
        is_owner = interaction.guild and interaction.user.id == interaction.guild.owner_id
        if interaction.guild_id != config.GUILD_ID or not (
            is_owner or self.authorize(interaction, "Marvel Rivals")
        ):
            await interaction.response.send_message(
                "Only the owner or Marvel Rivals Managers can change the scrim rank search.", ephemeral=True
            )
            return
        try:
            ranks = normalize_filter(min_rank, max_rank)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        self.service.store.set_scrim_rank_filter(config.SCRIM_OPPORTUNITIES_CHANNEL_ID, ranks)
        if self.publisher:
            await self.publisher.sync()
        selected = "Any rank" if ranks is None else (
            ranks[0] if ranks[0] == ranks[1] else f"{ranks[0]} to {ranks[1]}"
        )
        await interaction.followup.send(
            f"Default scrim search: {selected}. Matches opponent rank ranges entirely within this selection. "
            "Applies to channel posts and /scrim_opportunities.", ephemeral=True,
        )

    @rank_filter.autocomplete("min_rank")
    @rank_filter.autocomplete("max_rank")
    async def autocomplete_rank(self, interaction: discord.Interaction, current: str):
        return [app_commands.Choice(name=rank, value=rank) for rank in rank_suggestions(current)]

    @app_commands.command(
        name="scrim_opportunities", description="Game managers: preview collected scrim offers"
    )
    async def opportunities(self, interaction: discord.Interaction):
        if not self.authorize(interaction, "Marvel Rivals"):
            await interaction.response.send_message(
                "Only Marvel Rivals Managers can view scrim offers.", ephemeral=True
            )
            return
        feed = FeedStore(config.ROOT / "data" / "scrim_feed.sqlite3")
        ranks = self.service.store.scrim_rank_filter(config.SCRIM_OPPORTUNITIES_CHANNEL_ID)
        snapshot = feed.identified_offers()
        qualified = {key: offer for key, offer in snapshot.items() if rank_matches(offer, ranks)}
        offers = [qualified[key] for key, count in select_offers(qualified).values()]
        if not offers:
            await interaction.response.send_message(
                "No collected scrim offers are available.", ephemeral=True
            )
            return
        view = OfferPreview(offers, interaction.user.id)
        await interaction.response.send_message(embed=view.embed(), view=view, ephemeral=True)
