"""Small composition root: configuration, persistence, services, and cogs."""

import logging

import discord
from discord.ext import commands

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


class RionnagBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        intents.voice_states = True
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def setup_hook(self):
        store = Store(config.DATABASE)
        service = Applications(self, store, config.load_forms())
        self.tree.interaction_check = self.check_guild
        await self.add_cog(Health(self))
        await self.add_cog(Onboarding(self, service))
        await self.add_cog(Profiles(service))
        publisher = OpportunityPublisher(
            self,
            store,
            FeedStore(config.ROOT / "data" / "scrim_feed.sqlite3"),
            service.forms,
            config.GUILD_ID,
            config.SCRIM_OPPORTUNITIES_CHANNEL_ID,
        )
        await self.add_cog(Scrims(self, service, publisher))
        hosting = store.host_settings(config.SCRIM_HOST_CHANNEL_ID)
        destinations = posting_channels()
        if hosting["destination"] is None and len(destinations) == 1:
            store.configure_host(config.SCRIM_HOST_CHANNEL_ID, destination=next(iter(destinations)))
        await self.add_cog(ScrimHosting(HostingService(self, service, publisher.eligible_players)))
        self.tree.copy_global_to(guild=discord.Object(config.GUILD_ID))
        await self.tree.sync(guild=discord.Object(config.GUILD_ID))
        # Remove legacy public commands: this bot belongs to exactly one server.
        self.tree.clear_commands(guild=None)
        await self.tree.sync()

    async def check_guild(self, interaction):
        return interaction.guild_id == config.GUILD_ID


def main():
    from rionnag.instance import SingleInstance

    config.DATABASE.parent.mkdir(parents=True, exist_ok=True)
    logs = config.ROOT / "logs"
    logs.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(logs / "bot.log", encoding="utf-8")],
    )
    if not config.TOKEN:
        raise RuntimeError("Set DISCORD_TOKEN in .env.")
    with SingleInstance(config.ROOT / "data" / "bot.lock"):
        RionnagBot().run(config.TOKEN, log_handler=None)
