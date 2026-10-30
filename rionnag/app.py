"""Small composition root: configuration, persistence, services, and cogs."""

import logging

import discord
from discord.ext import commands

from rionnag import config
from rionnag.servers.registry import PROFILES, load_servers


class ServerCommandTree(discord.app_commands.CommandTree):
    def __init__(self, client, **kwargs):
        super().__init__(client, fallback_to_global=False, **kwargs)

    async def interaction_check(self, interaction):
        if interaction.guild_id not in {p.guild_id for p in self.client.server_profiles}:
            return False
        command = interaction.command
        while command is not None and getattr(command, "parent", None) is not None:
            command = command.parent
        return command is not None and command in self.get_commands(guild=interaction.guild)


class RionnagBot(commands.Bot):
    def __init__(self):
        self.server_profiles = PROFILES
        intents = discord.Intents.default()
        intents.members = True
        intents.voice_states = True
        intents.message_content = any(p.message_content for p in self.server_profiles)
        super().__init__(
            command_prefix=commands.when_mentioned,
            tree_cls=ServerCommandTree,
            intents=intents,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def setup_hook(self):
        await load_servers(self, self.server_profiles)
        for profile in self.server_profiles:
            await self.sync_server(profile.guild_id)
        # Clear legacy public commands; every feature belongs to an explicit server.
        self.tree.clear_commands(guild=None)
        await self.tree.sync()

    async def sync_server(self, guild_id):
        try:
            commands_synced = await self.tree.sync(guild=discord.Object(id=guild_id))
        except discord.Forbidden:
            logging.getLogger(__name__).warning("Server %s is unavailable for command sync", guild_id)
            return
        logging.getLogger(__name__).info("Synced %s commands for server %s", len(commands_synced), guild_id)

    async def on_guild_join(self, guild):
        if guild.id in {p.guild_id for p in self.server_profiles}:
            await self.sync_server(guild.id)


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
