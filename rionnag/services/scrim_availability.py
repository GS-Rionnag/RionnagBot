"""Conservative recurring availability, shared by both Discord runtimes."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

ZONES = {
    "Eastern Time (ET)": "America/New_York",
    "Central Time (CT)": "America/Chicago",
    "Mountain Time (MT)": "America/Denver",
    "Pacific Time (PT)": "America/Los_Angeles",
}


def day_windows(value):
    """Read both legacy single windows and the current list of windows."""
    if isinstance(value, dict):
        return [value]
    return value if isinstance(value, list) else []


def window_hours(window):
    if not isinstance(window, dict):
        return None
    start, end = window.get("start"), window.get("end")
    if type(start) is not int or type(end) is not int or not 0 <= start < 24:
        return None
    # Legacy windows used an end clock earlier than the start for overnight.
    if end == start:
        return None
    if 0 <= end <= 24 and end < start:
        end += 24
    if not start < end <= start + 24 or end > 48:
        return None
    return start, end


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
        for window in day_windows(days.get(date.strftime("%A"))):
            hours = window_hours(window)
            if hours:
                a, b = hours
                midnight = datetime.combine(date, datetime.min.time())
                left = local_boundary(midnight + timedelta(hours=a), zone, True)
                right = local_boundary(midnight + timedelta(hours=b), zone, False)
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
