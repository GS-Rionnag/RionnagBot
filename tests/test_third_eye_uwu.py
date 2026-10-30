import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord

from rionnag.servers.third_eye.cogs.uwu import UwuLocks
from rionnag.servers.third_eye.config import GUILD_ID
from rionnag.servers.third_eye.storage import LockStore


class LockTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "locks.sqlite3"
        self.cog = UwuLocks(SimpleNamespace(user=SimpleNamespace(id=99)), self.path)
        self.cog.store.set(42, True)
        self.hook = SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(delete=AsyncMock())))
        self.cog.webhook_for = AsyncMock(return_value=self.hook)
        self.channel = Mock(spec=discord.TextChannel)
        self.channel.id = 123
        self.channel.permissions_for.return_value = discord.Permissions.all()
        self.message = SimpleNamespace(
            guild=SimpleNamespace(id=GUILD_ID, me=object()), channel=self.channel,
            author=SimpleNamespace(id=42, bot=False, display_name="Alice", display_avatar=SimpleNamespace(
                url="https://cdn.discordapp.com/avatar.png")),
            webhook_id=None, content="hello everyone", attachments=[], stickers=[], poll=None,
            type=discord.MessageType.default, delete=AsyncMock())

    async def test_reposts_name_avatar_then_deletes_without_pings(self):
        events = []
        self.hook.send.side_effect = lambda **kwargs: (
            events.append("send") or SimpleNamespace(delete=AsyncMock()))
        self.message.delete.side_effect = lambda: events.append("delete")
        await self.cog.on_message(self.message)
        self.assertEqual(events, ["send", "delete"])
        kwargs = self.hook.send.call_args.kwargs
        self.assertIn("hewwo", kwargs["content"])
        self.assertEqual(kwargs["username"], "Alice")
        self.assertEqual(kwargs["avatar_url"], self.message.author.display_avatar.url)
        self.assertEqual(kwargs["allowed_mentions"].to_dict(), {"parse": []})

    async def test_failed_repost_preserves_original(self):
        self.hook.send.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "")
        await self.cog.on_message(self.message)
        self.message.delete.assert_not_awaited()

    async def test_url_only_messages_are_not_reposted_or_deleted(self):
        for content in ["https://example.com/hello?x=1&y=2", "  http://example.com  ",
                        "<https://example.com>", "www.example.com/path",
                        "https://example.com\nhttps://example.org", "HTTPS://EXAMPLE.COM"]:
            with self.subTest(content=content):
                self.message.content = content
                await self.cog.on_message(self.message)
        self.cog.webhook_for.assert_not_awaited()
        self.hook.send.assert_not_awaited()
        self.message.delete.assert_not_awaited()

    async def test_text_with_url_still_triggers(self):
        self.message.content = "hello https://example.com"
        await self.cog.on_message(self.message)
        self.hook.send.assert_awaited_once()
        self.message.delete.assert_awaited_once()

    async def test_failed_delete_rolls_back_repost(self):
        self.message.delete.side_effect = discord.Forbidden(
            SimpleNamespace(status=403, reason="Forbidden"), "")
        await self.cog.on_message(self.message)
        self.hook.send.return_value.delete.assert_awaited_once()

    async def test_attachments_are_preserved(self):
        file = discord.File(__file__)
        attachment = SimpleNamespace(to_file=AsyncMock(return_value=file))
        self.message.attachments = [attachment]
        await self.cog.on_message(self.message)
        attachment.to_file.assert_awaited_once()
        self.assertIn(file, self.hook.send.call_args.kwargs["files"])
        self.message.delete.assert_awaited_once()

    async def test_long_output_is_attached_not_truncated(self):
        self.message.content = "hello " * 500
        await self.cog.on_message(self.message)
        kwargs = self.hook.send.call_args.kwargs
        self.assertLessEqual(len(kwargs["content"]), 2000)
        self.assertEqual(kwargs["files"][0].filename, "uwu-message.txt")

    async def test_ignores_other_guild_unlocked_bots_webhooks_and_stickers(self):
        for field, value in [("guild", SimpleNamespace(id=1)), ("webhook_id", 5),
                             ("stickers", [object()]), ("poll", object())]:
            old = getattr(self.message, field)
            setattr(self.message, field, value)
            await self.cog.on_message(self.message)
            setattr(self.message, field, old)
        self.message.author.bot = True
        await self.cog.on_message(self.message)
        self.message.author.bot = False
        self.cog.store.set(42, False)
        await self.cog.on_message(self.message)
        self.hook.send.assert_not_awaited()
        self.message.delete.assert_not_awaited()

    async def test_missing_permissions_preserves_original(self):
        self.channel.permissions_for.return_value = discord.Permissions.none()
        await self.cog.on_message(self.message)
        self.hook.send.assert_not_awaited()
        self.message.delete.assert_not_awaited()

    async def test_lock_survives_restart_and_unlock(self):
        self.assertIn(42, LockStore(self.path).users)
        self.cog.store.set(42, False)
        self.assertNotIn(42, LockStore(self.path).users)

    async def test_lock_requires_moderation_permission(self):
        interaction = SimpleNamespace(guild_id=GUILD_ID, permissions=discord.Permissions.none(),
                                      response=SimpleNamespace(send_message=AsyncMock()))
        await self.cog.set_lock(interaction, SimpleNamespace(id=7), True)
        self.assertNotIn(7, self.cog.store.users)
        self.assertTrue(interaction.response.send_message.call_args.kwargs["ephemeral"])

    async def test_command_toggles_persisted_lock_on_then_off(self):
        interaction = SimpleNamespace(
            guild_id=GUILD_ID, permissions=discord.Permissions.all(),
            app_permissions=discord.Permissions.all(),
            response=SimpleNamespace(send_message=AsyncMock()))
        member = SimpleNamespace(id=7, bot=False, display_name="Bob")
        await self.cog.lock.callback(self.cog, interaction, member)
        self.assertIn(7, LockStore(self.path).users)
        await self.cog.lock.callback(self.cog, interaction, member)
        self.assertNotIn(7, LockStore(self.path).users)
        self.assertEqual([p.name for p in self.cog.lock.parameters], ["member"])
