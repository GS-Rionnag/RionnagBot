"""Private-log health summaries that never return log messages or tracebacks."""

import re
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path

HEADER = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) (ERROR|CRITICAL) ([\w.]+):"
)


def recent_errors(path: Path, now: datetime | None = None) -> str:
    cutoff = (now or datetime.now()) - timedelta(hours=24)
    latest = deque(maxlen=5)
    count = 0
    try:
        with path.open(encoding="utf-8", errors="replace") as stream:
            for line in stream:
                match = HEADER.match(line)
                if not match:
                    continue
                try:
                    timestamp = datetime.strptime(match[1], "%Y-%m-%d %H:%M:%S,%f")
                except ValueError:
                    continue
                if cutoff <= timestamp <= (now or datetime.now()):
                    count += 1
                    latest.append(f"{timestamp:%Y-%m-%d %H:%M:%S} — {match[3][:100]}")
    except OSError:
        return "Error log unavailable; recent errors could not be checked."
    if not count:
        return "No ERROR/CRITICAL entries in the last 24 hours."
    return (
        f"{count} ERROR/CRITICAL log entries in the last 24 hours (may include duplicates)."
        "\nLatest entries, in server log time:\n" + "\n".join(latest)
    )
