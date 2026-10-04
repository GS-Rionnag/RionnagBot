"""Show saved form data with Discord timestamps for viewer-local times."""

from datetime import UTC, datetime

import discord

from rionnag import config
from rionnag.ui.availability import schedule_text


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
    current = now or datetime.now(UTC)
    embed.add_field(name="Time zone", value=time_zone or "Not saved")
    embed.add_field(name="Current time", value=f"<t:{int(current.timestamp())}:t>")
    days = answers.get("availability_days", {})
    embed.add_field(name="Days and times free",
                    value=(schedule_text(days, time_zone, current)
                           or answers.get("availability") or "Not saved")[:1024],
                    inline=False)
    embed.set_footer(text="Times display in your Discord local time zone.")
    return embed
