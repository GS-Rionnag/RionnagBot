"""Composition for RIONNAG eSports; existing domain workflows stay unchanged."""

import discord

from rionnag import config
from rionnag.cogs.health import Health
from rionnag.cogs.onboarding import Onboarding
from rionnag.cogs.profiles import Profiles
from rionnag.cogs.scrim_hosting import ScrimHosting, posting_channels
from rionnag.cogs.scrims import Scrims
from rionnag.scrims.scrim_feed import FeedStore
from rionnag.services.applications import Applications
from rionnag.services.scrim_hosting import HostingService
from rionnag.services.scrim_opportunities import OpportunityPublisher
from rionnag.storage import Store


async def setup(bot, profile):
    guild = discord.Object(id=profile.guild_id)
    store = Store(config.DATABASE)
    service = Applications(bot, store, config.load_forms())
    await bot.add_cog(Health(bot), guild=guild)
    await bot.add_cog(Onboarding(bot, service), guild=guild)
    await bot.add_cog(Profiles(service), guild=guild)
    publisher = OpportunityPublisher(
        bot, store, FeedStore(config.ROOT / "data" / "scrim_feed.sqlite3"),
        service.forms, profile.guild_id, config.SCRIM_OPPORTUNITIES_CHANNEL_ID,
    )
    scrims = Scrims(bot, service, publisher)
    await bot.add_cog(scrims, guild=guild)
    hosting = store.host_settings(config.SCRIM_HOST_CHANNEL_ID)
    destinations = posting_channels()
    if hosting["destination"] is None and len(destinations) == 1:
        store.configure_host(config.SCRIM_HOST_CHANNEL_ID, destination=next(iter(destinations)))
    hosting_service = HostingService(bot, service, publisher.eligible_players)
    scrims.controller.upcoming_status = hosting_service.status_embed
    await bot.add_cog(ScrimHosting(hosting_service), guild=guild)
