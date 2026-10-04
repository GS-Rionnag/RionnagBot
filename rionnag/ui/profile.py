"""Show a member's saved form using their own selected local time zone."""

from datetime import UTC, datetime

import discord

from rionnag import config
from rionnag.ui.availability import DAYS, hour_label, selected_zone


def form_profile_embed(member, form, answers, now=None):
    embed = discord.Embed(title=f"{member.display_name} · {form['name']}", color=config.COLOR)
    time_zone = answers.get("time_zone")
    for question in form["questions"]:
        key = question["key"]
        if key in {"time_zone", "availability"}:
            continue
        value = str(answers.get(key) or "Not saved")
        label = "In-game username" if key == "username" else question["label"]
        embed.add_field(name=label[:256], value=value[:1024], inline=False)
        if len(embed.fields) >= 22:
            break
    local = (now or datetime.now(UTC)).astimezone(selected_zone(time_zone)) if time_zone else None
    embed.add_field(name="Time zone", value=time_zone or "Not saved")
    embed.add_field(name="Your local time", value=local.strftime("%A, %I:%M %p %Z") if local else "Not saved")
    days = answers.get("availability_days", {})
    lines = []
    for day in DAYS:
        if day in days:
            start, end = days[day]["start"], days[day]["end"]
            suffix = " (next day)" if end <= start or end == 24 else ""
            lines.append(f"{day}: {hour_label(start)} – {hour_label(end)}{suffix}")
    embed.add_field(name="Days and times free (your local time)",
                    value=("\n".join(lines) or answers.get("availability") or "Not saved")[:1024],
                    inline=False)
    return embed
