"""Remove obsolete duplicate bot welcome prompts while retaining each registered message."""

import asyncio

import discord

from rionnag import config
from rionnag.storage import Store


async def main():
    removed = 0
    async with discord.Client(intents=discord.Intents.none()) as client:
        await client.login(config.TOKEN)
        store = Store(config.DATABASE)
        for member_id in store.ids():
            row = store.member(member_id)
            if not row["channel_id"] or not row["message_id"]:
                continue
            channel = await client.fetch_channel(row["channel_id"])
            async for message in channel.history(limit=50):
                welcome = any(embed.title == "Welcome to Rionnag" for embed in message.embeds)
                welcome = welcome or message.content.startswith("Welcome to Rionnag.")
                if message.author.id == client.user.id and message.id != row["message_id"] and welcome:
                    await message.delete()
                    removed += 1
    print("Removed obsolete duplicate welcome prompts:", removed)


if __name__ == "__main__":
    asyncio.run(main())
