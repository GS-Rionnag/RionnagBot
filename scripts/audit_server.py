"""Read-only deployment audit. Prints IDs/status counts, never credentials or answers."""

import asyncio
from collections import Counter

import discord

from rionnag import config
from rionnag.storage import Store


async def main():
    intents = discord.Intents.none()
    intents.members = True
    async with discord.Client(intents=intents) as client:
        await client.login(config.TOKEN)
        guild = await client.fetch_guild(config.GUILD_ID)
        channels = await guild.fetch_channels()
        roles = await guild.fetch_roles()
        owner = await guild.fetch_member(guild.owner_id)
        me = await guild.fetch_member(client.user.id)
        print("Guild:", guild.id, "owner:", guild.owner_id)
        print("Owner roles:", [role.id for role in owner.roles])
        print("Bot role position:", me.top_role.position)
        members = [member async for member in guild.fetch_members(limit=None) if not member.bot]
        print(
            "Human members:",
            len(members),
            "live membership role counts:",
            dict(
                Counter(
                    role.id
                    for member in members
                    for role in member.roles
                    if role.id not in {config.GUILD_ID, config.OWNER_ROLE_ID}
                )
            ),
        )
        print(
            "Membership role positions:",
            {
                role.id: role.position
                for role in roles
                if role.id
                in {config.VISITOR_ROLE_ID, 1554272432309797015, 1554275227901632512, 1554281222023421992}
            },
        )
        print("Channels:", [(channel.id, channel.name, channel.category_id) for channel in channels])
        if config.DATABASE.exists():
            store = Store(config.DATABASE)
            print("Onboarding statuses:", dict(Counter(store.member(mid)["status"] for mid in store.ids())))
            row = store.member(guild.owner_id)
            print("Owner ticket:", row["channel_id"], "saved role IDs:", row["restore_roles"])
            for channel in channels:
                if channel.id not in {config.ENTRY_CHANNEL_ID, row["channel_id"]}:
                    continue
                async for message in channel.history(limit=1):
                    print(
                        "Panel:",
                        channel.name,
                        "embed titles:",
                        [embed.title for embed in message.embeds],
                        "buttons:",
                        [
                            (button.label, str(button.emoji))
                            for component in message.components
                            for button in component.children
                            if isinstance(button, discord.Button)
                        ],
                    )


if __name__ == "__main__":
    asyncio.run(main())
