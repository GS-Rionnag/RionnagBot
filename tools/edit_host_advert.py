"""Edit one explicitly selected own advert over HTTP without starting a gateway client."""

import argparse
import asyncio
import os
import re

import discord
from dotenv import load_dotenv

from rionnag import config
from rionnag.storage import Store


async def edit(channel_id, message_id):
    load_dotenv(config.ROOT / "scrim_collector" / ".env", override=True)
    raw = os.getenv("SCRIM_HOST_CHANNEL_IDS") or os.getenv("SCRIM_SOURCE_CHANNEL_IDS", "")
    if channel_id not in {int(cid.strip()) for cid in raw.split(",") if cid.strip().isdigit()}:
        raise ValueError("Channel is not an allowed advert destination")
    async with discord.Client() as client:
        await client.login(os.environ["DISCORD_USER_TOKEN"])
        channel = await client.fetch_channel(channel_id)
        message = await channel.fetch_message(message_id)
        if message.author.id != client.user.id:
            raise ValueError("The selected message is not owned by the posting account")
        match = re.search(r"<t:(\d+):F>", message.content)
        if not match or not message.content.startswith("LFS"):
            raise ValueError("The selected message is not a timestamped LFS advert")
        content = f"LFS Grandmaster - Celestial at <t:{match[1]}:F>"
        await message.edit(content=content, allowed_mentions=discord.AllowedMentions.none())
        verified = await channel.fetch_message(message_id)
        if verified.content != content:
            raise RuntimeError("Advert edit could not be verified")
        with Store(config.DATABASE).connection() as db:
            db.execute("UPDATE scrim_host_adverts SET content=? WHERE destination=? AND message_id=? "
                       "AND status='sent'", (content, channel_id, message_id))
        print("Existing advert edited and verified; date/time preserved, player list removed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("channel_id", type=int)
    parser.add_argument("message_id", type=int)
    args = parser.parse_args()
    asyncio.run(edit(args.channel_id, args.message_id))
