"""Upload the bundled scrim stat images as server emojis; safe to rerun."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import discord
from dotenv import load_dotenv


async def main():
    load_dotenv()
    root = Path(__file__).parent / "assets" / "scrim_icons"
    async with discord.Client(intents=discord.Intents.none()) as client:
        await client.login(os.environ["DISCORD_TOKEN"])
        guild = await client.fetch_guild(int(os.environ["GUILD_ID"]))
        emojis = await guild.fetch_emojis()
        for stat in ("damage", "blocked", "healing", "accuracy"):
            name = f"mr_{stat}"
            emoji = discord.utils.get(emojis, name=name)
            if emoji is None:
                image = (root / f"{stat}.png").read_bytes()
                if not image.startswith(b"\x89PNG\r\n\x1a\n") or len(image) > 256 * 1024:
                    raise ValueError(f"Invalid emoji image: {stat}")
                emoji = await guild.create_custom_emoji(
                    name=name,
                    image=image,
                    reason="Use clearer stat icons in scrim result embeds",
                )
            print(f"{stat}: {emoji}")


if __name__ == "__main__":
    asyncio.run(main())
