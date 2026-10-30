"""The Third Eye's commands and automatic webhook replacements."""

import asyncio
import io
import logging

import discord
from discord import app_commands
from discord.ext import commands

from rionnag.servers.third_eye.config import GUILD_ID
from rionnag.servers.third_eye.storage import LockStore
from rionnag.servers.third_eye.text import only_urls, transform

log = logging.getLogger(__name__)


class UwuLocks(commands.Cog):
    def __init__(self, bot, state_path):
        self.bot = bot
        self.store = LockStore(state_path)
        self.webhooks = {}
        self.channel_locks = {}

    async def set_lock(self, interaction, member, enabled):
        if interaction.guild_id != GUILD_ID:
            await interaction.response.send_message("Use this in The Third Eye.", ephemeral=True)
            return
        if not interaction.permissions.manage_messages:
            await interaction.response.send_message("You need Manage Messages to use this.", ephemeral=True)
            return
        if member.bot:
            await interaction.response.send_message("Choose a person, not a bot.", ephemeral=True)
            return
        if enabled:
            perms = interaction.app_permissions
            if not (perms.manage_messages and perms.manage_webhooks):
                await interaction.response.send_message(
                    "I need Manage Messages and Manage Webhooks in this channel first.", ephemeral=True)
                return
        self.store.set(member.id, enabled)
        action = "UwU lock enabled" if enabled else "UwU lock disabled"
        await interaction.response.send_message(
            f"{action} for {member.display_name} across this server.", ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none())

    @app_commands.command(name="uwuify", description="Toggle automatic uwuification for a member")
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.describe(member="Whose uwu lock to toggle")
    async def lock(self, interaction: discord.Interaction, member: discord.Member):
        await self.set_lock(interaction, member, member.id not in self.store.users)

    async def webhook_for(self, channel):
        if channel.id not in self.webhooks:
            hooks = await channel.webhooks()
            hook = next((h for h in hooks if h.name == "UwU lock"
                         and h.user and h.user.id == self.bot.user.id and h.token), None)
            self.webhooks[channel.id] = hook or await channel.create_webhook(
                name="UwU lock", reason="The Third Eye uwu lock command")
        return self.webhooks[channel.id]

    @commands.Cog.listener()
    async def on_message(self, message):
        if (not message.guild or message.guild.id != GUILD_ID or message.author.bot
                or message.webhook_id or message.author.id not in self.store.users
                or message.type not in (discord.MessageType.default, discord.MessageType.reply)
                or message.stickers or message.poll):
            return
        if not message.content and not message.attachments:
            return
        if only_urls(message.content):
            return
        thread = message.channel if isinstance(message.channel, discord.Thread) else None
        channel = thread.parent if thread else message.channel
        if not isinstance(channel, discord.TextChannel):
            return
        perms = message.channel.permissions_for(message.guild.me)
        if not (perms.manage_messages and perms.manage_webhooks and perms.view_channel
                and (perms.send_messages_in_threads if thread else perms.send_messages)):
            return
        lock = self.channel_locks.setdefault(message.channel.id, asyncio.Lock())
        async with lock:
            if message.author.id not in self.store.users:
                return
            files = []
            try:
                # Deliver the complete replacement before removing the original.
                for attachment in message.attachments:
                    files.append(await attachment.to_file())
                hook = await self.webhook_for(channel)
                text = transform(message.content)
                if len(text) > 2000:
                    files.append(discord.File(io.BytesIO(text.encode("utf-8")), filename="uwu-message.txt"))
                    text = "UwUified message attached (too long for Discord)."
                if len(files) > 10:
                    return
                options = {"files": files}
                if thread:
                    options["thread"] = thread
                repost = await hook.send(
                    content=text or None, username=message.author.display_name[:80],
                    avatar_url=str(message.author.display_avatar.url),
                    allowed_mentions=discord.AllowedMentions.none(), wait=True, **options)
                try:
                    await message.delete()
                except discord.HTTPException:
                    # If deletion fails, roll back the duplicate replacement.
                    await repost.delete()
                    raise
            except (discord.HTTPException, OSError) as error:
                self.webhooks.pop(channel.id, None)
                # Never log message bodies, attachment URLs, or webhook credentials.
                log.warning("UwU replacement failed in channel %s (%s)", channel.id, type(error).__name__)
            finally:
                for file in files:
                    file.close()
