"""One permission policy for tickets, main channels, and tryout entry."""

import discord

from rionnag import config


def has_role(member, role_id):
    return any(role.id == role_id for role in member.roles)


def game_member(member, forms):
    return any(
        has_role(member, form[k])
        for form in forms.values()
        for k in ("team_role", "tryout_role", "manager_role")
    )


def visitor_eligible(member, forms):
    return has_role(member, config.VISITOR_ROLE_ID) and not game_member(member, forms)


def removable_roles(member):
    return [
        role
        for role in member.roles
        if not role.is_default() and not role.managed and role.id != config.OWNER_ROLE_ID
    ]


def ticket_overwrites(member, form=None):
    guild = member.guild
    overwrites = {
        role: discord.PermissionOverwrite(view_channel=False) for role in guild.roles if not role.is_default()
    }
    overwrites[guild.default_role] = discord.PermissionOverwrite(view_channel=False)
    overwrites[member] = discord.PermissionOverwrite(
        view_channel=True, send_messages=True, read_message_history=True
    )
    overwrites[guild.me] = discord.PermissionOverwrite(
        view_channel=True, send_messages=True, read_message_history=True, manage_channels=True
    )
    if form:
        role = guild.get_role(form["manager_role"])
        if role is None:
            raise ValueError("Configured game manager role is missing.")
        overwrites[role] = discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True
        )
    return overwrites


async def apply_server_policy(guild, forms, store):
    """Overwrite visibility only; preserve unrelated channel capabilities."""
    entry = guild.get_channel(config.ENTRY_CHANNEL_ID)
    category = guild.get_channel(config.APPLICATIONS_CATEGORY_ID)
    voice_rooms = {}
    if store is not None:
        with store.connection() as db:
            configured = db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='scrim_config'"
            ).fetchone()
            voice_rooms = dict(db.execute(
                "SELECT waiting_id, stage_id FROM scrim_config WHERE guild_id=?", (guild.id,)
            )) if configured else {}
    for channel in guild.channels:
        # The owner controls finder visibility, including hiding it during testing.
        if channel.id == config.SCRIM_OPPORTUNITIES_CHANNEL_ID:
            continue
        if (
            channel.id == config.APPLICATIONS_CATEGORY_ID
            or channel.category_id == config.APPLICATIONS_CATEGORY_ID
        ):
            continue
        if not hasattr(channel, "overwrites"):
            continue
        overwrites = dict(channel.overwrites)
        everyone = overwrites.get(guild.default_role, discord.PermissionOverwrite())
        everyone.view_channel = False
        overwrites[guild.default_role] = everyone
        # Remove member-specific bypasses for members who must finish onboarding.
        for target, overwrite in list(overwrites.items()):
            if (
                isinstance(target, discord.Member)
                and not target.bot
                and store.member(target.id)["status"] not in {"accepted", "visitor", "rejected"}
            ):
                del overwrites[target]
        for role in guild.roles:
            if role.is_default() or role.managed or role.id == config.OWNER_ROLE_ID:
                continue
            overwrite = overwrites.get(role, discord.PermissionOverwrite())
            if channel == entry:
                # Visitor's allow would override team denies. Team members never retain Visitor.
                overwrite.view_channel = role.id == config.VISITOR_ROLE_ID
                overwrite.send_messages = False
                overwrite.read_message_history = True
            elif role.id == config.VISITOR_ROLE_ID:
                marvel = channel.category_id == 1555382746992353290 or channel.id == 1555382746992353290
                overwrite.view_channel = (
                    channel.category_id == 1555732719583895622 or channel.id == 1555732719583895622
                ) or marvel
                if marvel:
                    overwrite.read_message_history = True
                    overwrite.send_messages = False
                    overwrite.send_messages_in_threads = False
                    overwrite.create_public_threads = False
                    overwrite.create_private_threads = False
                    overwrite.connect = False
                    overwrite.speak = False
                    if channel.id in voice_rooms or channel.id in voice_rooms.values():
                        overwrite.connect = True
                        overwrite.speak = channel.id in voice_rooms
            elif any(
                role.id in (f["team_role"], f["tryout_role"], f["manager_role"]) for f in forms.values()
            ):
                overwrite.view_channel = channel.category_id in (1555732719583895622, 1555382746992353290)
                if channel.category_id == 1555658298211045446:
                    overwrite.view_channel = False
            overwrites[role] = overwrite
        if channel.overwrites != overwrites:
            await channel.edit(overwrites=overwrites, reason="Rionnag onboarding visibility policy")
    if category:
        await category.edit(
            overwrites={guild.default_role: discord.PermissionOverwrite(view_channel=False)},
            reason="Private application category",
        )
