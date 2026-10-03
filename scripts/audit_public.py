"""Check staged/current history for private state and known credentials without printing them."""

import re
import subprocess
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]


def git(*args):
    return subprocess.check_output(["rtk", "proxy", "git", *args], cwd=ROOT, stderr=subprocess.DEVNULL)


def main():
    values = []
    for env in (ROOT / ".env", ROOT / "scrim_collector" / ".env"):
        if env.exists():
            values += [
                value.encode()
                for key, value in dotenv_values(env).items()
                if value and len(value) > 20 and any(name in key for name in ("TOKEN", "SECRET", "KEY"))
            ]
    patterns = [
        rb"gh[pousr]_[A-Za-z0-9]{30,}",
        rb"mfa\.[A-Za-z0-9_-]{40,}",
        rb"[A-Za-z0-9_-]{24,}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{25,}",
    ]
    failures = set()
    sources = [(":", name.decode()) for name in git("ls-files", "-z").split(b"\0") if name]
    for commit in git("rev-list", "--all").decode().splitlines():
        sources += [
            (commit + ":", name) for name in git("ls-tree", "-r", "--name-only", commit).decode().splitlines()
        ]
    for prefix, name in sources:
        path = Path(name)
        if (
            path.name == ".env"
            or path.suffix in {".sqlite3", ".db", ".log"}
            or name.startswith(("data/", "logs/", "permission_backups/", "data_backups/"))
        ):
            failures.add(name)
        data = git("show", prefix + name)
        if any(value in data for value in values) or any(re.search(pattern, data) for pattern in patterns):
            failures.add(name)
    if failures:
        raise RuntimeError("Private material found in: " + ", ".join(sorted(failures)))
    print(
        "Public-source audit passed: no known credentials, tokens, databases, logs, or backups in index/history."
    )


if __name__ == "__main__":
    main()
