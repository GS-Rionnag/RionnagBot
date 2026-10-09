"""Conservative recurring availability, shared by both Discord runtimes."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

ZONES = {
    "Eastern Time (ET)": "America/New_York",
    "Central Time (CT)": "America/Chicago",
    "Mountain Time (MT)": "America/Denver",
    "Pacific Time (PT)": "America/Los_Angeles",
}


def same_scrim_day(start, official):
    """Compare session start dates in the server's scheduling time zone."""
    if start is None or official is None:
        return False
    zone = ZoneInfo("America/New_York")
    return datetime.fromtimestamp(start, zone).date() == datetime.fromtimestamp(official, zone).date()


def local_boundary(wall, zone, is_start):
    """Skip nonexistent clocks; use the conservative side of ambiguous clocks."""
    values = []
    for fold in (0, 1):
        local = wall.replace(tzinfo=zone, fold=fold)
        instant = local.timestamp()
        if datetime.fromtimestamp(instant, zone).replace(tzinfo=None) == wall:
            values.append(instant)
    return (max(values) if is_start else min(values)) if values else None


def covers_interval(answers, start, end):
    zone_name = ZONES.get(answers.get("time_zone"))
    days = answers.get("availability_days")
    if not zone_name or not isinstance(days, dict) or not days:
        return False
    zone = ZoneInfo(zone_name)
    first = datetime.fromtimestamp(start, zone).date() - timedelta(days=1)
    last = datetime.fromtimestamp(end, zone).date()
    intervals = []
    date = first
    while date <= last:
        window = days.get(date.strftime("%A"))
        if isinstance(window, dict):
            a, b = window.get("start"), window.get("end")
            if type(a) is int and type(b) is int and 0 <= a < 24 and 0 <= b <= 24 and a != b:
                midnight = datetime.combine(date, datetime.min.time())
                left = local_boundary(midnight + timedelta(hours=a), zone, True)
                right = local_boundary(midnight + timedelta(hours=b + (24 if b <= a else 0)), zone, False)
                if left is not None and right is not None and right > left:
                    intervals.append((left, right))
        date += timedelta(days=1)
    covered = start
    for left, right in sorted(intervals):
        if left > covered:
            break
        if right >= covered:
            covered = right
        if covered >= end:
            return True
    return False
