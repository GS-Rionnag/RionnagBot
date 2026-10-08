import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from scripts.consolidate_scrim_voice import consolidate


class VoiceMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_consolidation_preserves_tracking_restores_mutes_and_removes_only_extra_room(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "data").mkdir()
            database = root / "data" / "test.sqlite3"
            data = dict(status="playing", waiting_id=11, stage_id=12, number=3,
                        voice_mutes_before={"7": False}, overwrites_before={}, sync_error="old")
            with closing(sqlite3.connect(database)) as db:
                db.executescript("""
                    CREATE TABLE scrim_config(guild_id,game,control_id,waiting_id,stage_id);
                    CREATE TABLE scrim_lobbies(id,guild_id,game,data);
                    CREATE TABLE scrim_sessions(id,guild_id,game,data);
                    INSERT INTO scrim_config VALUES(1,'Marvel Rivals',10,11,12);
                """)
                for table in ("scrim_lobbies", "scrim_sessions"):
                    db.execute(f"INSERT INTO {table} VALUES(1,1,'Marvel Rivals',?)", (json.dumps(data),))
                db.commit()
            keep, old = (MagicMock(spec=discord.VoiceChannel) for _ in range(2))
            old.name = "Scrim"
            member = SimpleNamespace(id=7, voice=SimpleNamespace(channel=old, mute=True),
                                     edit=AsyncMock(), move_to=AsyncMock())
            old.members = [member]
            role = SimpleNamespace(id=5)
            keep.overwrites_for.return_value = discord.PermissionOverwrite()
            guild = SimpleNamespace(id=1, get_channel={11: keep, 12: old}.get,
                                    get_member=lambda _: member, get_role=lambda _: role)
            client = SimpleNamespace(get_guild=lambda _: guild)
            with patch.multiple("scripts.consolidate_scrim_voice.config", ROOT=root, DATABASE=database,
                                GUILD_ID=1, VISITOR_ROLE_ID=5), patch(
                "scripts.consolidate_scrim_voice.config.load_forms", return_value={}
            ):
                with self.assertRaises(RuntimeError):
                    await consolidate(client, 11)
                old.delete.assert_not_awaited()
                await consolidate(client, 12)
            member.edit.assert_awaited_once()
            self.assertFalse(member.edit.call_args.kwargs["mute"])
            member.move_to.assert_awaited_once_with(keep, reason=member.move_to.call_args.kwargs["reason"])
            old.delete.assert_awaited_once()
            keep.delete.assert_not_awaited()
            with closing(sqlite3.connect(database)) as db:
                mapping = db.execute("SELECT waiting_id,stage_id FROM scrim_config").fetchone()
                self.assertEqual(mapping, (11, 11))
                saved = json.loads(db.execute("SELECT data FROM scrim_sessions").fetchone()[0])
                self.assertEqual((saved["status"], saved["number"]), ("playing", 3))
                self.assertNotIn("voice_mutes_before", saved)
            self.assertTrue((root / "data" / "before_single_scrim_voice.sqlite3").exists())
