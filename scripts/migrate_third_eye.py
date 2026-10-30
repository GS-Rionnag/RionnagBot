"""Copy legacy uwu locks once; run with the old standalone process stopped."""

import sqlite3
from contextlib import closing
from pathlib import Path

from rionnag import config


def migrate(source: Path, destination: Path) -> str:
    if destination.exists():
        return "already_migrated"
    if not source.exists():
        return "no_legacy_database"
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".migrating")
    try:
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as original:
            # Validate before publishing a database into the canonical runtime.
            original.execute("SELECT user_id FROM locks LIMIT 1").fetchall()
            with closing(sqlite3.connect(temporary)) as target:
                original.backup(target)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return "migrated"


if __name__ == "__main__":
    print(migrate(config.ROOT.parent / "trollbot" / "data" / "locks.sqlite3",
                  config.ROOT / "data" / "servers" / "third_eye" / "locks.sqlite3"))
