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
    async def on_ready(self):
        if not self.restored:
            await self.controller.restore()
            self.restored = True

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
        offers = feed.offers()["scrims"]
        if not offers:
            await interaction.response.send_message(
                "No collected scrim offers are available.", ephemeral=True
            )
            return
        view = OfferPreview(offers, interaction.user.id)
        await interaction.response.send_message(embed=view.embed(), view=view, ephemeral=True)
