"""Restore this server's existing scrim channels after the authorized rebuild wipe."""

import asyncio

import discord

from rionnag import config
from rionnag.scrims.scrims import ScrimStore
from rionnag.storage import Store


async def main():
    store = ScrimStore(Store(config.DATABASE).connection)
    async with discord.Client(intents=discord.Intents.none()) as client:
        await client.login(config.TOKEN)
        ids = (1555304393014906890, 1555304432818855976, 1555398841333850113)
        channels = [await client.fetch_channel(cid) for cid in ids]
        if any(channel.guild.id != config.GUILD_ID for channel in channels):
            raise RuntimeError("Scrim channels belong to another server")
        if not isinstance(channels[0], discord.TextChannel) or not all(
            isinstance(c, discord.VoiceChannel) for c in channels[1:]
        ):
            raise RuntimeError("Unexpected scrim channel types")
        if store.configured():
            raise RuntimeError("Existing configuration is present; refusing to replace it")
        store.configure(config.GUILD_ID, "Marvel Rivals", *ids)
        lobby = store.lobby(config.GUILD_ID, "Marvel Rivals")
        async for message in channels[0].history(limit=20):
            if message.author.id == client.user.id and message.components and message.embeds:
                lobby["message_id"] = message.id
                store.save_lobby(lobby)
                break
        print("Existing scrim channels restored; dashboard will refresh on bot restart.")


if __name__ == "__main__":
    asyncio.run(main())
