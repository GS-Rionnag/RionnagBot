"""Private, server-specific uwu lock persistence."""

import sqlite3
from contextlib import closing
from pathlib import Path


class LockStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS locks (user_id INTEGER PRIMARY KEY)")
            self.users = {row[0] for row in db.execute("SELECT user_id FROM locks")}

    def set(self, user_id: int, enabled: bool):
        with closing(sqlite3.connect(self.path)) as db, db:
            if enabled:
                db.execute("INSERT OR IGNORE INTO locks VALUES (?)", (user_id,))
            else:
                db.execute("DELETE FROM locks WHERE user_id = ?", (user_id,))
        if enabled:
            self.users.add(user_id)
        else:
            self.users.discard(user_id)
