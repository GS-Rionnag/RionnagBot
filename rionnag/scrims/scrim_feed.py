"""Durable message inbox and scrim feed shared by the two bot processes."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from rionnag.scrims.scrim_offer_rules import compact_offer, eastern_local_times, offer_lifecycle, timestamp


class FeedStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS feed_messages (
                    id TEXT PRIMARY KEY, payload TEXT NOT NULL, revision INTEGER NOT NULL,
                    processed INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS feed_offers (
                    message_id TEXT NOT NULL, position INTEGER NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(message_id, position));
                CREATE TABLE IF NOT EXISTS feed_history (
                    channel_id TEXT PRIMARY KEY, last_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS feed_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        try:
            with db:
                yield db
        finally:
            db.close()

    def put(self, message: dict):
        payload = json.dumps(message, ensure_ascii=False)
        with self.connect() as db:
            cursor = db.execute(
                """INSERT INTO feed_messages(id,payload,revision) VALUES(?,?,1)
                ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,
                revision=revision+1, processed=0 WHERE payload != excluded.payload""",
                (message["id"], payload),
            )
            if cursor.rowcount:
                # A changed source invalidates all previous interpretations at once.
                db.execute("DELETE FROM feed_offers WHERE message_id=?", (message["id"],))

    def history_cursor(self, channel_id: int):
        with self.connect() as db:
            row = db.execute(
                "SELECT last_id FROM feed_history WHERE channel_id=?", (str(channel_id),)
            ).fetchone()
            return int(row[0]) if row else None

    def upgrade_extraction(self, version: str):
        with self.connect() as db:
            row = db.execute("SELECT value FROM feed_settings WHERE key='extraction_version'").fetchone()
            if row == (version,):
                return 0
            count = db.execute("""UPDATE feed_messages SET processed=0
                WHERE id IN (SELECT DISTINCT message_id FROM feed_offers)""").rowcount
            db.execute("DELETE FROM feed_offers")
            db.execute("INSERT OR REPLACE INTO feed_settings VALUES('extraction_version',?)", (version,))
            return count

    def cache_history(self, message: dict):
        # Save the message before advancing the checkpoint. A crash between these
        # commits only causes an identical cached message to be read again.
        self.put(message)
        with self.connect() as db:
            db.execute(
                """INSERT INTO feed_history VALUES(?,?) ON CONFLICT(channel_id)
                DO UPDATE SET last_id=excluded.last_id
                WHERE CAST(excluded.last_id AS INTEGER) > CAST(last_id AS INTEGER)""",
                (message["channel_id"], message["id"]),
            )

    def delete(self, message_id: str):
        with self.connect() as db:
            db.execute("DELETE FROM feed_offers WHERE message_id=?", (message_id,))
            db.execute("DELETE FROM feed_messages WHERE id=?", (message_id,))

    def pending(self, limit=100):
        with self.connect() as db:
            return [
                (mid, revision, json.loads(payload))
                for mid, revision, payload in db.execute(
                    "SELECT id,revision,payload FROM feed_messages WHERE processed=0 ORDER BY rowid LIMIT ?",
                    (limit,),
                )
            ]

    def pending_count(self):
        with self.connect() as db:
            return db.execute("SELECT COUNT(*) FROM feed_messages WHERE processed=0").fetchone()[0]

    def compact_saved_offers(self):
        with self.connect() as db:
            rows = list(db.execute("SELECT message_id,position,payload FROM feed_offers"))
            db.executemany(
                "UPDATE feed_offers SET payload=? WHERE message_id=? AND position=?",
                [
                    (json.dumps(compact_offer(json.loads(payload)), ensure_ascii=False), mid, position)
                    for mid, position, payload in rows
                ],
            )
        return len(rows)

    def repair_eastern_times(self):
        changed = 0
        with self.connect() as db:
            rows = list(
                db.execute("""SELECT o.message_id,o.position,o.payload,m.payload
                FROM feed_offers o JOIN feed_messages m ON m.id=o.message_id""")
            )
            for mid, position, payload, source in rows:
                offer = json.loads(payload)
                fixed = eastern_local_times(offer, json.loads(source))
                if fixed != offer:
                    db.execute(
                        "UPDATE feed_offers SET payload=? WHERE message_id=? AND position=?",
                        (json.dumps(fixed, ensure_ascii=False), mid, position),
                    )
                    changed += 1
        return changed

    def retire_old_messages(self, cutoff):
        with self.connect() as db:
            rows = list(db.execute("SELECT id,payload FROM feed_messages WHERE processed=0"))
            old = [
                (mid,)
                for mid, payload in rows
                if (timestamp(json.loads(payload).get("created_at")) or 0) < cutoff
            ]
            db.executemany("UPDATE feed_messages SET processed=1 WHERE id=?", old)
        return len(old)

    def prune_expired(self, now=None):
        now = time.time() if now is None else now
        with self.connect() as db:
            rows = list(db.execute("SELECT message_id,position,payload FROM feed_offers"))
            expired = []
            for mid, position, payload in rows:
                _, expiry = offer_lifecycle(
                    json.loads(payload), now, os.getenv("SCRIM_DAY_TIMEZONE", "America/New_York")
                )
                if expiry is not None and now >= expiry:
                    expired.append((mid, position))
            db.executemany("DELETE FROM feed_offers WHERE message_id=? AND position=?", expired)
        return len(expired)

    def complete(self, batch, result):
        offers = result["scrims"]
        by_id = {mid: [] for mid, _, _ in batch}
        for offer in offers:
            mid = offer["source_message_id"]
            if mid not in by_id:
                raise ValueError("Extraction referenced a message outside its batch")
            by_id[mid].append(offer)
        with self.connect() as db:
            for mid, revision, message in batch:
                row = db.execute("SELECT revision FROM feed_messages WHERE id=?", (mid,)).fetchone()
                if row != (revision,):
                    continue  # An edit or deletion arrived while extraction ran.
                db.execute("DELETE FROM feed_offers WHERE message_id=?", (mid,))
                for position, offer in enumerate(by_id[mid]):
                    enriched = compact_offer(offer, message)
                    db.execute(
                        "INSERT INTO feed_offers VALUES(?,?,?)",
                        (mid, position, json.dumps(enriched, ensure_ascii=False)),
                    )
                db.execute("UPDATE feed_messages SET processed=1 WHERE id=?", (mid,))

    def offers(self):
        self.prune_expired()
        with self.connect() as db:
            offers = [
                json.loads(row[0])
                for row in db.execute("SELECT payload FROM feed_offers ORDER BY message_id,position")
            ]
        return {"scrims": [compact_offer(offer) for offer in offers]}
