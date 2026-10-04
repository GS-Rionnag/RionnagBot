"""Durable workflow state. No Discord calls or permissions belong here."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path


class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS members (
                    member_id INTEGER PRIMARY KEY, status TEXT NOT NULL DEFAULT 'new',
                    game TEXT, version INTEGER NOT NULL DEFAULT 0, answers TEXT NOT NULL DEFAULT '{}',
                    channel_id INTEGER, message_id INTEGER, restore_roles TEXT,
                    restore_status TEXT, reset_target INTEGER, dm_pending TEXT, membership_roles TEXT);
                CREATE TABLE IF NOT EXISTS form_versions (game TEXT PRIMARY KEY, version INTEGER NOT NULL,
                    definition TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS player_profiles (
                    guild_id INTEGER, member_id INTEGER, game TEXT, username TEXT NOT NULL,
                    time_zone TEXT NOT NULL, preferred_role_1 TEXT NOT NULL, preferred_role_2 TEXT NOT NULL,
                    updated_at REAL NOT NULL, PRIMARY KEY(guild_id,member_id,game));
                CREATE TABLE IF NOT EXISTS player_availability (
                    guild_id INTEGER, member_id INTEGER, game TEXT, data TEXT NOT NULL,
                    PRIMARY KEY(guild_id,member_id,game));
            """)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def member(self, member_id: int) -> dict:
        with self.connection() as db:
            db.execute("INSERT OR IGNORE INTO members(member_id) VALUES(?)", (member_id,))
            row = dict(db.execute("SELECT * FROM members WHERE member_id=?", (member_id,)).fetchone())
        row["answers"] = json.loads(row["answers"])
        row["restore_roles"] = json.loads(row["restore_roles"]) if row["restore_roles"] is not None else None
        row["membership_roles"] = json.loads(row["membership_roles"]) if row["membership_roles"] else []
        return row

    def update(self, member_id: int, **values):
        allowed = {
            "status",
            "game",
            "version",
            "answers",
            "channel_id",
            "message_id",
            "restore_roles",
            "restore_status",
            "reset_target",
            "dm_pending",
            "membership_roles",
        }
        if not values.keys() <= allowed:
            raise ValueError("Unknown member fields")
        self.member(member_id)
        values = {
            key: json.dumps(value)
            if key in {"answers", "restore_roles", "membership_roles"} and value is not None
            else value
            for key, value in values.items()
        }
        with self.connection() as db:
            db.execute(
                "UPDATE members SET " + ",".join(f"{key}=?" for key in values) + " WHERE member_id=?",
                (*values.values(), member_id),
            )

    def ids(self):
        with self.connection() as db:
            return [row[0] for row in db.execute("SELECT member_id FROM members")]

    def delete_member(self, guild_id, member_id):
        """Erase saved membership, answers, caches, and game profile atomically."""
        with self.connection() as db:
            db.execute("DELETE FROM player_profiles WHERE guild_id=? AND member_id=?", (guild_id, member_id))
            db.execute(
                "DELETE FROM player_availability WHERE guild_id=? AND member_id=?", (guild_id, member_id)
            )
            db.execute("DELETE FROM members WHERE member_id=?", (member_id,))

    def check_form(self, key: str, form: dict):
        definition = json.dumps(form, sort_keys=True)
        with self.connection() as db:
            row = db.execute("SELECT version,definition FROM form_versions WHERE game=?", (key,)).fetchone()
            if row and (form["version"] < row[0] or (form["version"] == row[0] and definition != row[1])):
                raise ValueError(
                    f"{key}: increment the version when changing a form; versions cannot decrease."
                )

    def record_form(self, key: str, form: dict):
        with self.connection() as db:
            db.execute(
                "INSERT OR REPLACE INTO form_versions VALUES(?,?,?)",
                (key, form["version"], json.dumps(form, sort_keys=True)),
            )

    def saved_profile(self, guild_id, member_id, game="Marvel Rivals"):
        with self.connection() as db:
            row = db.execute(
                "SELECT username,time_zone,preferred_role_1,preferred_role_2 FROM player_profiles "
                "WHERE guild_id=? AND member_id=? AND game=?",
                (guild_id, member_id, game),
            ).fetchone()
            return tuple(row) if row else None

    def save_profile(self, guild_id, member_id, form, answers):
        import time

        with self.connection() as db:
            db.execute(
                "INSERT OR REPLACE INTO player_profiles VALUES(?,?,?,?,?,?,?,?)",
                (
                    guild_id,
                    member_id,
                    form["name"],
                    answers["username"],
                    answers["time_zone"],
                    answers["preferred_role_1"],
                    answers["preferred_role_2"],
                    time.time(),
                ),
            )

    def saved_uid(self, guild_id, member_id, game):
        with self.connection() as db:
            row = db.execute(
                "SELECT m.answers FROM members m JOIN player_profiles p ON p.member_id=m.member_id "
                "WHERE p.guild_id=? AND p.member_id=? AND p.game=? AND m.status='accepted'",
                (guild_id, member_id, game),
            ).fetchone()
        return json.loads(row[0]).get("player_uid") if row else None

    def remove_profile(self, guild_id, member_id):
        with self.connection() as db:
            db.execute("DELETE FROM player_profiles WHERE guild_id=? AND member_id=?", (guild_id, member_id))
