from __future__ import annotations

import os
from pathlib import Path

import discord
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID", "0"))
ICON_PATH = Path(__file__).parent / "assets" / "marvel_rivals_logo.png"
ROLE_NAMES = (
    "Visitor",
    "Marvel Rivals",
    "Marvel Rivals Tryout",
    "Marvel Rivals Manager",
    "Recruitment Manager",
)


class IconSetup(discord.Client):
    async def on_ready(self) -> None:
        guild = self.get_guild(GUILD_ID)
        if guild is None:
            print(f"Configured server {GUILD_ID} is not available to the bot.")
            await self.close()
            return

        image = ICON_PATH.read_bytes()
        emoji = discord.utils.get(guild.emojis, name="marvelrivals")
        if emoji is None:
            emoji = await guild.create_custom_emoji(
                name="marvelrivals",
                image=image,
                reason="Add the Marvel Rivals icon to onboarding controls",
            )
            print(f"Created :{emoji.name}: ({emoji.id})")
        else:
            print(f"Using existing :{emoji.name}: ({emoji.id})")

        if "ROLE_ICONS" in guild.features:
            for name in ROLE_NAMES:
                role = discord.utils.get(guild.roles, name=name)
                if role is None:
                    continue
                await role.edit(
                    display_icon=image,
                    reason="Use the Marvel Rivals mark for onboarding and game roles",
                )
                print(f"Added icon to role {name}")
        else:
            print("This server does not have the ROLE_ICONS feature; buttons can use the emoji, roles cannot show role icons.")

        await self.close()


if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN is missing. Add it to .env.")

intents = discord.Intents.none()
intents.guilds = True
client = IconSetup(intents=intents)
client.run(TOKEN, log_handler=None)
