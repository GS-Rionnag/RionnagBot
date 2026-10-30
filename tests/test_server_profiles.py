import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

from rionnag import config
from rionnag.app import RionnagBot
from rionnag.servers.registry import PROFILES, load_servers
from rionnag.servers.third_eye.storage import LockStore
from scripts.migrate_third_eye import migrate


class ServerTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_modules_register_and_route_only_their_guild_commands(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "data").mkdir()
            with (patch.object(config, "ROOT", root),
                  patch.object(config, "DATABASE", root / "data/org.sqlite3")):
                async with RionnagBot() as bot:
                    bot.tree.sync = AsyncMock(return_value=[])
                    await bot.setup_hook()
                    org, third = (discord.Object(id=p.guild_id) for p in PROFILES)
                    original = bot.tree.get_commands(guild=org)
                    other = bot.tree.get_commands(guild=third)
                    self.assertEqual(len(original), 12)
                    self.assertEqual({c.name for c in other}, {"uwuify"})
                    self.assertFalse(bot.tree.get_commands())
                    self.assertFalse(bot.tree.fallback_to_global)
                    self.assertTrue(bot.intents.message_content)
                    self.assertEqual(bot.tree.sync.await_args_list[0].kwargs["guild"].id, org.id)
                    self.assertEqual(bot.tree.sync.await_args_list[1].kwargs["guild"].id, third.id)
                    self.assertEqual(bot.tree.sync.await_args_list[2].kwargs, {})
                    for command in original:
                        self.assertTrue(await bot.tree.interaction_check(
                            SimpleNamespace(guild_id=org.id, guild=org, command=command)))
                        self.assertFalse(await bot.tree.interaction_check(
                            SimpleNamespace(guild_id=third.id, guild=third, command=command)))
                    self.assertTrue(await bot.tree.interaction_check(
                        SimpleNamespace(guild_id=third.id, guild=third, command=other[0])))
                    self.assertFalse(await bot.tree.interaction_check(
                        SimpleNamespace(guild_id=org.id, guild=org, command=other[0])))
                    self.assertFalse(await bot.tree.interaction_check(
                        SimpleNamespace(guild_id=None, guild=None, command=other[0])))
                    group = next(c for c in original if c.name == "scrim")
                    self.assertTrue(await bot.tree.interaction_check(
                        SimpleNamespace(guild_id=org.id, guild=org, command=group.commands[0])))

    async def test_unknown_server_join_does_not_sync_or_enable_features(self):
        async with RionnagBot() as bot:
            bot.sync_server = AsyncMock()
            await bot.on_guild_join(discord.Object(id=123))
            bot.sync_server.assert_not_awaited()
            await bot.on_guild_join(discord.Object(id=PROFILES[1].guild_id))
            bot.sync_server.assert_awaited_once_with(PROFILES[1].guild_id)

    async def test_duplicate_registry_is_rejected_before_loading_modules(self):
        async with RionnagBot() as bot:
            with self.assertRaises(ValueError):
                await load_servers(bot, (PROFILES[0], PROFILES[0]))

    async def test_missing_server_cannot_stop_other_server_sync(self):
        async with RionnagBot() as bot:
            denied = discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "")
            bot.tree.sync = AsyncMock(side_effect=[denied, []])
            await bot.sync_server(PROFILES[0].guild_id)
            await bot.sync_server(PROFILES[1].guild_id)
            self.assertEqual(bot.tree.sync.await_count, 2)


class MigrationTests(unittest.TestCase):
    def test_database_backup_preserves_locks_and_does_not_restore_unlocked_users_on_rerun(self):
        with tempfile.TemporaryDirectory() as folder:
            source, destination = Path(folder)/"legacy.sqlite3", Path(folder)/"new/locks.sqlite3"
            LockStore(source).set(42, True)
            self.assertEqual(migrate(source, destination), "migrated")
            self.assertEqual(LockStore(destination).users, {42})
            LockStore(destination).set(42, False)
            self.assertEqual(migrate(source, destination), "already_migrated")
            self.assertEqual(LockStore(destination).users, set())
            with closing(sqlite3.connect(source)) as db:
                self.assertEqual(db.execute("SELECT user_id FROM locks").fetchall(), [(42,)])
