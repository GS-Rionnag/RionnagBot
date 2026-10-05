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
                CREATE TABLE IF NOT EXISTS scrim_opportunity_posts (
                    channel_id INTEGER NOT NULL, offer_key TEXT NOT NULL,
                    message_id INTEGER, fingerprint TEXT, status TEXT NOT NULL DEFAULT 'pending',
                    pending_since REAL NOT NULL,
                    PRIMARY KEY(channel_id,offer_key));
                CREATE TABLE IF NOT EXISTS scrim_opportunity_votes (
                    channel_id INTEGER NOT NULL, offer_key TEXT NOT NULL, member_id INTEGER NOT NULL,
                    PRIMARY KEY(channel_id,offer_key,member_id));
                CREATE TABLE IF NOT EXISTS scrim_search_settings (
                    channel_id INTEGER PRIMARY KEY, min_rank TEXT NOT NULL, max_rank TEXT NOT NULL);
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(scrim_opportunity_posts)")}
            for name, kind in (("start_time", "INTEGER"), ("source_key", "TEXT"),
                               ("source_revision", "INTEGER"), ("source_author", "TEXT")):
                if name not in columns:
                    db.execute(f"ALTER TABLE scrim_opportunity_posts ADD COLUMN {name} {kind}")

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
            db.execute("DELETE FROM scrim_opportunity_votes WHERE member_id=?", (member_id,))

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

    def profile_names(self, guild_id, game="Marvel Rivals"):
        with self.connection() as db:
            return dict(db.execute(
                "SELECT member_id,username FROM player_profiles WHERE guild_id=? AND game=?",
                (guild_id, game),
            ).fetchall())

    def scrim_candidates(self, guild_id, game, version):
        """Only accepted, current-form players with a restored game profile."""
        with self.connection() as db:
            rows = db.execute(
                "SELECT m.member_id,m.answers FROM members m JOIN player_profiles p "
                "ON p.member_id=m.member_id WHERE p.guild_id=? AND p.game=? "
                "AND m.game=? AND m.status='accepted' AND m.version=? AND m.restore_roles IS NULL",
                (guild_id, "Marvel Rivals", game, version),
            ).fetchall()
        return [{"member_id": row[0], "answers": json.loads(row[1])} for row in rows]

    def opportunity_posts(self, channel_id):
        with self.connection() as db:
            return {row["offer_key"]: dict(row) for row in db.execute(
                "SELECT * FROM scrim_opportunity_posts WHERE channel_id=?", (channel_id,)
            )}

    def scrim_rank_filter(self, channel_id):
        with self.connection() as db:
            row = db.execute("SELECT min_rank,max_rank FROM scrim_search_settings WHERE channel_id=?",
                             (channel_id,)).fetchone()
        return tuple(row) if row else None

    def set_scrim_rank_filter(self, channel_id, ranks):
        with self.connection() as db:
            if ranks is None:
                db.execute("DELETE FROM scrim_search_settings WHERE channel_id=?", (channel_id,))
            else:
                db.execute("INSERT OR REPLACE INTO scrim_search_settings VALUES(?,?,?)", (channel_id, *ranks))

    def reset_opportunity_posts(self, channel_id):
        """Explicit operator rebuild only; retain source feed and search settings."""
        with self.connection() as db:
            db.execute("DELETE FROM scrim_opportunity_votes WHERE channel_id=?", (channel_id,))
            db.execute("DELETE FROM scrim_opportunity_posts WHERE channel_id=?", (channel_id,))

    def reserve_opportunity(self, channel_id, offer_key, start_time=None, source_key=None,
                            source_revision=None, source_author=None):
        import time

        with self.connection() as db:
            db.execute(
                "INSERT INTO scrim_opportunity_posts(channel_id,offer_key,pending_since,"
                "start_time,source_key,source_revision,source_author) VALUES(?,?,?,?,?,?,?) "
                "ON CONFLICT(channel_id,offer_key) DO UPDATE SET "
                "status='pending', pending_since=excluded.pending_since, "
                "start_time=COALESCE(excluded.start_time,scrim_opportunity_posts.start_time), "
                "source_key=COALESCE(excluded.source_key,scrim_opportunity_posts.source_key), "
                "source_revision=COALESCE(excluded.source_revision,scrim_opportunity_posts.source_revision), "
                "source_author=COALESCE(excluded.source_author,scrim_opportunity_posts.source_author) "
                "WHERE scrim_opportunity_posts.status='inactive' "
                "AND scrim_opportunity_posts.message_id IS NULL",
                (channel_id, offer_key, time.time(), start_time, source_key, source_revision, source_author),
            )

    def save_opportunity(self, channel_id, offer_key, message_id, fingerprint, status, start_time=None,
                         source_key=None, source_revision=None, source_author=None, reset_votes=False):
        self.reserve_opportunity(channel_id, offer_key)
        with self.connection() as db:
            db.execute(
                "UPDATE scrim_opportunity_posts SET message_id=?,fingerprint=?,status=?, "
                "start_time=COALESCE(?,start_time), source_key=COALESCE(?,source_key), "
                "source_revision=COALESCE(?,source_revision), source_author=COALESCE(?,source_author) "
                "WHERE channel_id=? AND offer_key=?",
                (message_id, fingerprint, status, start_time, source_key, source_revision, source_author,
                 channel_id, offer_key),
            )
            if reset_votes:
                db.execute("DELETE FROM scrim_opportunity_votes WHERE channel_id=? AND offer_key=?",
                           (channel_id, offer_key))

    def opportunity_votes(self, channel_id, offer_key):
        with self.connection() as db:
            return [row[0] for row in db.execute(
                "SELECT member_id FROM scrim_opportunity_votes WHERE channel_id=? AND offer_key=? "
                "ORDER BY member_id", (channel_id, offer_key),
            )]

    def vote_opportunity(self, channel_id, offer_key, member_id, add):
        with self.connection() as db:
            # The write lock makes the six-person cap atomic across connections.
            db.execute("BEGIN IMMEDIATE")
            if add:
                count = db.execute(
                    "SELECT COUNT(*) FROM scrim_opportunity_votes WHERE channel_id=? AND offer_key=?",
                    (channel_id, offer_key),
                ).fetchone()[0]
                exists = db.execute(
                    "SELECT 1 FROM scrim_opportunity_votes "
                    "WHERE channel_id=? AND offer_key=? AND member_id=?",
                    (channel_id, offer_key, member_id),
                ).fetchone()
                if count >= 6 and not exists:
                    raise ValueError("Six players have already voted for this scrim.")
                db.execute("INSERT OR IGNORE INTO scrim_opportunity_votes VALUES(?,?,?)",
                           (channel_id, offer_key, member_id))
            else:
                db.execute(
                    "DELETE FROM scrim_opportunity_votes WHERE channel_id=? AND offer_key=? AND member_id=?",
                    (channel_id, offer_key, member_id),
                )

    def clear_opportunity_votes(self, channel_id, offer_key, valid_ids=None):
        with self.connection() as db:
            if valid_ids is None:
                db.execute("DELETE FROM scrim_opportunity_votes WHERE channel_id=? AND offer_key=?",
                           (channel_id, offer_key))
            else:
                rows = db.execute(
                    "SELECT member_id FROM scrim_opportunity_votes WHERE channel_id=? AND offer_key=?",
                    (channel_id, offer_key),
                ).fetchall()
                db.executemany(
                    "DELETE FROM scrim_opportunity_votes WHERE channel_id=? AND offer_key=? AND member_id=?",
                    [(channel_id, offer_key, row[0]) for row in rows if row[0] not in valid_ids],
                )

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
