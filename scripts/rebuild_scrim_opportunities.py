"""Owner-authorized channel rebuild. Stop the running bot before using this script."""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import discord  # noqa: E402

from rionnag import config  # noqa: E402
from rionnag.instance import SingleInstance  # noqa: E402
from rionnag.scrims.scrim_feed import FeedStore  # noqa: E402
from rionnag.services.scrim_opportunities import OpportunityPublisher  # noqa: E402
from rionnag.storage import Store  # noqa: E402


class Rebuild(discord.Client):
    async def on_ready(self):
        if getattr(self, "rebuilding", False):
            return
        self.rebuilding = True
        try:
            guild = self.get_guild(config.GUILD_ID)
            channel = guild.get_channel(config.SCRIM_OPPORTUNITIES_CHANNEL_ID)
            if not isinstance(channel, discord.TextChannel):
                raise RuntimeError("Configured scrim finder text channel is unavailable")
            deleted = 0
            async for message in channel.history(limit=None):
                try:
                    await message.delete()
                except discord.NotFound:
                    pass
                deleted += 1
            print("deleted_messages", deleted)
            overwrites = dict(channel.overwrites)
            # The owner explicitly requested visibility for everyone in this channel only.
            for target, overwrite in overwrites.items():
                if overwrite.view_channel is False:
                    overwrite.view_channel = None
            everyone = overwrites.get(guild.default_role, discord.PermissionOverwrite())
            everyone.view_channel = True
            everyone.read_message_history = True
            overwrites[guild.default_role] = everyone
            channel = await channel.edit(overwrites=overwrites,
                                         reason="Owner authorized public scrim rebuild")
            print("everyone_can_view", channel.overwrites_for(guild.default_role).view_channel)
            store = Store(config.DATABASE)
            store.reset_opportunity_posts(channel.id)
            feed = FeedStore(config.ROOT / "data/scrim_feed.sqlite3")
            publisher = OpportunityPublisher(self, store, feed, config.load_forms(), guild.id, channel.id)
            await publisher.sync()
            posts = store.opportunity_posts(channel.id)
            print("new_posts", sum(p["status"] == "active" for p in posts.values()))
        finally:
            await self.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-channel", type=int, required=True,
                        help="Explicitly confirm deletion of every message in the finder channel")
    args = parser.parse_args()
    if args.confirm_channel != config.SCRIM_OPPORTUNITIES_CHANNEL_ID:
        parser.error("Confirmation must match the configured scrim finder channel")
    intents = discord.Intents.default()
    intents.members = True
    # Use the bot's lock so a live publisher cannot race with deletion/reposting.
    with SingleInstance(config.ROOT / "data/bot.lock"):
        asyncio.run(run(intents))


async def run(intents):
    async with Rebuild(intents=intents) as client:
        await client.start(config.TOKEN)


if __name__ == "__main__":
    main()
