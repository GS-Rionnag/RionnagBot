"""Read-only hosting readiness checks; never print credentials or player answers."""

import asyncio

import discord

from rionnag import config
from rionnag.cogs.scrim_hosting import posting_channels
from rionnag.storage import Store


async def main():
    async with discord.Client(intents=discord.Intents.none()) as client:
        await client.login(config.TOKEN)
        channel = await client.fetch_channel(config.SCRIM_HOST_CHANNEL_ID)
        print(f"Hosting channel: {channel.id}, type={channel.type}, guild={channel.guild.id}")
        print(f"Configured advert destinations: {len(posting_channels())}")
        store = Store(config.DATABASE)
        print(f"Saved finder rank filter: {store.scrim_rank_filter(config.SCRIM_OPPORTUNITIES_CHANNEL_ID)}")
        settings = store.host_settings(config.SCRIM_HOST_CHANNEL_ID)
        if settings["message_id"]:
            message = await channel.fetch_message(settings["message_id"])
            print(f"Hosting board present: {bool(message.embeds)}, controls={len(message.components)} rows")
            print(
                "Public buttons: "
                + ", ".join(
                    item.label
                    for row in message.components
                    for item in row.children
                    if hasattr(item, "label")
                )
            )
            if message.embeds:
                print(f"Public session fields: {len(message.embeds[0].fields)}")
            print(f"Board link: {message.jump_url}")
        else:
            print("Hosting board has not been delivered yet")


if __name__ == "__main__":
    asyncio.run(main())
