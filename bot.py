from __future__ import annotations

import asyncio
import logging
import os
import re
import sqlite3
import time
import unicodedata
from pathlib import Path

import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID", "0"))
DATABASE_PATH = Path(os.getenv("ONBOARDING_DB_PATH", "onboarding.sqlite3"))
DEFAULT_EMBED_COLOR = discord.Color.from_rgb(125, 0, 255)
MARVEL_EMOJI_ID = int(os.getenv("MARVEL_EMOJI_ID", "0"))
MARVEL_EMOJI_NAME = os.getenv("MARVEL_EMOJI_NAME", "MR")
CUSTOM_EMOJI_ENABLED = True
MANAGER_ROLE_NAME = os.getenv("MANAGER_ROLE_NAME", "Recruitment Manager")
VISITOR_ROLE_NAME = os.getenv("VISITOR_ROLE_NAME", "Visitor")
GAMES = tuple(
    name.strip()
    for name in os.getenv("GAMES", "Marvel Rivals").split(",")
    if name.strip()
)

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN is missing. Add it to .env.")

intents = discord.Intents.default()
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)
initialized = False
ticket_creation_locks: dict[tuple[int, int], asyncio.Lock] = {}


def marvel_emoji(guild: discord.Guild | None = None) -> discord.PartialEmoji | None:
    if not MARVEL_EMOJI_ID or not CUSTOM_EMOJI_ENABLED:
        return None
    if guild is not None:
        emoji = guild.get_emoji(MARVEL_EMOJI_ID)
        if emoji is None:
            return None
        return emoji
    return discord.PartialEmoji(name=MARVEL_EMOJI_NAME, id=MARVEL_EMOJI_ID)


def initialize_state() -> float | None:
    """Create the durable ticket registry and return the prior startup time."""
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DATABASE_PATH) as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS tickets ("
            "guild_id INTEGER NOT NULL, member_id INTEGER NOT NULL, channel_id INTEGER NOT NULL, "
            "status TEXT NOT NULL DEFAULT 'Unreviewed', game TEXT, username TEXT, updated_at REAL NOT NULL, "
            "PRIMARY KEY (guild_id, member_id))"
        )
        columns = {row[1] for row in db.execute("PRAGMA table_info(tickets)")}
        if "game" not in columns:
            db.execute("ALTER TABLE tickets ADD COLUMN game TEXT")
        if "username" not in columns:
            db.execute("ALTER TABLE tickets ADD COLUMN username TEXT")
        db.execute("CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        db.execute("UPDATE tickets SET status = 'Unreviewed' WHERE status = 'Tryout'")
        row = db.execute("SELECT value FROM state WHERE key = 'last_started_at'").fetchone()
        previous_start = float(row[0]) if row else None
    return previous_start


def mark_started() -> None:
    with sqlite3.connect(DATABASE_PATH) as db:
        db.execute(
            "INSERT INTO state(key, value) VALUES('last_started_at', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(time.time()),),
        )


def ticket_for(guild_id: int, member_id: int) -> tuple[int, str] | None:
    with sqlite3.connect(DATABASE_PATH) as db:
        row = db.execute(
            "SELECT channel_id, status FROM tickets WHERE guild_id = ? AND member_id = ?",
            (guild_id, member_id),
        ).fetchone()
    return (int(row[0]), str(row[1])) if row else None


def save_ticket(guild_id: int, member_id: int, channel_id: int, status: str = "Unreviewed") -> None:
    with sqlite3.connect(DATABASE_PATH) as db:
        db.execute(
            "INSERT INTO tickets(guild_id, member_id, channel_id, status, updated_at) VALUES(?, ?, ?, ?, ?) "
            "ON CONFLICT(guild_id, member_id) DO UPDATE SET channel_id = excluded.channel_id, "
            "status = excluded.status, updated_at = excluded.updated_at",
            (guild_id, member_id, channel_id, status, time.time()),
        )


def save_application(guild_id: int, member_id: int, channel_id: int, game: str, username: str) -> None:
    with sqlite3.connect(DATABASE_PATH) as db:
        db.execute(
            "UPDATE tickets SET channel_id = ?, status = ?, game = ?, username = ?, updated_at = ? "
            "WHERE guild_id = ? AND member_id = ?",
            (channel_id, f"Pending: {game}", game, username, time.time(), guild_id, member_id),
        )


def ticket_application(guild_id: int, member_id: int) -> tuple[str | None, str | None] | None:
    with sqlite3.connect(DATABASE_PATH) as db:
        row = db.execute(
            "SELECT game, username FROM tickets WHERE guild_id = ? AND member_id = ?",
            (guild_id, member_id),
        ).fetchone()
    return (row[0], row[1]) if row else None


def list_tickets(guild_id: int) -> list[tuple[int, int]]:
    with sqlite3.connect(DATABASE_PATH) as db:
        return [
            (int(member_id), int(channel_id))
            for member_id, channel_id in db.execute(
                "SELECT member_id, channel_id FROM tickets WHERE guild_id = ?", (guild_id,)
            ).fetchall()
        ]


def remove_ticket(guild_id: int, member_id: int) -> None:
    with sqlite3.connect(DATABASE_PATH) as db:
        db.execute("DELETE FROM tickets WHERE guild_id = ? AND member_id = ?", (guild_id, member_id))


def member_for_ticket_channel(guild_id: int, channel_id: int) -> int | None:
    with sqlite3.connect(DATABASE_PATH) as db:
        row = db.execute(
            "SELECT member_id FROM tickets WHERE guild_id = ? AND channel_id = ?",
            (guild_id, channel_id),
        ).fetchone()
    return int(row[0]) if row else None


def role_named(guild: discord.Guild, name: str) -> discord.Role | None:
    return discord.utils.get(guild.roles, name=name)


def can_manage_applicant(interaction: discord.Interaction, game: str | None = None) -> bool:
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        return False
    if interaction.user.id == interaction.guild.owner_id:
        return True
    manager = role_named(interaction.guild, MANAGER_ROLE_NAME)
    if manager and manager in interaction.user.roles:
        return True
    if game:
        game_manager = role_named(interaction.guild, f"{game} Manager")
        return bool(game_manager and game_manager in interaction.user.roles)
    return False


class ApplicantActions(discord.ui.View):
    """Persistent self-service controls for visitors and game tryout applications."""

    def __init__(self) -> None:
        super().__init__(timeout=None)
        self.add_item(VisitorAction())
        for index, game in enumerate(GAMES[:20]):
            self.add_item(GameTryoutAction(game, index))


class GameTryoutAction(discord.ui.Button):
    def __init__(self, game: str, index: int) -> None:
        self.game = game
        super().__init__(
            label=f"{game} Tryouts",
            custom_id=f"applicant:apply:{index}",
            style=discord.ButtonStyle.primary,
            emoji=marvel_emoji(),
            row=index // 5 + 1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        applicant_id = ticket_member_for_interaction(interaction)
        if applicant_id != interaction.user.id:
            await interaction.response.send_message("Only the applicant can submit a tryout application here.", ephemeral=True)
            return
        await interaction.response.send_modal(TryoutApplicationModal(self.game))


class VisitorAction(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="I'm a visitor",
            custom_id="applicant:visitor",
            style=discord.ButtonStyle.secondary,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if ticket_member_for_interaction(interaction) != interaction.user.id:
            await interaction.response.send_message("Only the applicant can choose Visitor here.", ephemeral=True)
            return
        await assign_role_and_close(interaction, VISITOR_ROLE_NAME, None)


def ticket_member_for_interaction(interaction: discord.Interaction) -> int | None:
    if not interaction.guild or not isinstance(interaction.channel, discord.TextChannel):
        return None
    return member_for_ticket_channel(interaction.guild.id, interaction.channel.id)


class TryoutApplicationModal(discord.ui.Modal):
    def __init__(self, game: str) -> None:
        super().__init__(title=f"{game} tryout application", timeout=300)
        self.game = game
        self.add_item(
            discord.ui.TextDisplay(
                "Use your main account and turn on public stats so managers can view your profile and verify your actual rank. "
                "Your username will be shared with the relevant managers in this private ticket."
            )
        )
        self.player_name = discord.ui.TextInput(
            label=f"{game} username",
            placeholder="Enter your in-game username",
            min_length=1,
            max_length=100,
            required=True,
        )
        self.add_item(self.player_name)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if ticket_member_for_interaction(interaction) != interaction.user.id:
            await interaction.response.send_message("This isn't your application channel.", ephemeral=True)
            return
        if not interaction.guild or not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("This form only works in your application channel.", ephemeral=True)
            return
        manager_role = role_named(interaction.guild, f"{self.game} Manager")
        if manager_role is None:
            await interaction.response.send_message(
                f"The {self.game} Manager role is not set up yet. Please contact the server owner.", ephemeral=True
            )
            return

        applicant = interaction.user
        assert isinstance(applicant, discord.Member)
        username = str(self.player_name.value).strip()
        save_application(interaction.guild.id, applicant.id, interaction.channel.id, self.game, username)
        await interaction.channel.set_permissions(
            manager_role,
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            manage_messages=True,
        )
        embed = discord.Embed(
            title=f"{self.game} tryout application",
            description=f"**In-game username:** {discord.utils.escape_markdown(username)}\n\nReview the application and approve the tryout role if appropriate.",
            color=DEFAULT_EMBED_COLOR,
        )
        await interaction.response.send_message(
            f"Your application was sent to the {self.game} managers. They'll review it here.", ephemeral=True
        )
        await interaction.channel.send(
            content=f"{manager_role.mention} Tryout application from {applicant.mention}.",
            embed=embed,
            view=ReviewActions(self.game),
            allowed_mentions=discord.AllowedMentions(roles=[manager_role], users=[applicant], everyone=False),
        )


class ReviewActions(discord.ui.View):
    def __init__(self, game: str) -> None:
        super().__init__(timeout=None)
        index = GAMES.index(game)
        self.add_item(ApproveTryoutAction(game, index))


class ApproveTryoutAction(discord.ui.Button):
    def __init__(self, game: str, game_index: int) -> None:
        self.game = game
        super().__init__(
            label=f"Approve {game} tryout",
            custom_id=f"applicant:approve:{game_index}",
            style=discord.ButtonStyle.success,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not can_manage_applicant(interaction, self.game):
            await interaction.response.send_message(
                f"Only the server owner, Recruitment Managers, or {self.game} Managers can approve this.",
                ephemeral=True,
            )
            return
        applicant_id = ticket_member_for_interaction(interaction)
        member = interaction.guild.get_member(applicant_id) if interaction.guild and applicant_id else None
        if member is None or not interaction.guild or not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("I couldn't find the applicant for this ticket.", ephemeral=True)
            return
        application = ticket_application(interaction.guild.id, member.id)
        if not application or application[0] != self.game or not application[1]:
            await interaction.response.send_message("There is no pending application for this game.", ephemeral=True)
            return
        await assign_role_and_close(interaction, f"{self.game} Tryout", self.game)


async def assign_role_and_close(
    interaction: discord.Interaction, role_name: str, game: str | None
) -> None:
    if not interaction.guild or not isinstance(interaction.channel, discord.TextChannel):
        await interaction.response.send_message("This action only works in an applicant channel.", ephemeral=True)
        return

    applicant_id = member_for_ticket_channel(interaction.guild.id, interaction.channel.id)
    applicant = interaction.guild.get_member(applicant_id) if applicant_id else None
    if applicant is None:
        await interaction.response.send_message("I couldn't find this channel's applicant.", ephemeral=True)
        return

    selected_role = role_named(interaction.guild, role_name)
    visitor = role_named(interaction.guild, VISITOR_ROLE_NAME)
    tryout_roles = [role_named(interaction.guild, f"{name} Tryout") for name in GAMES]
    if selected_role is None or visitor is None or any(role is None for role in tryout_roles):
        await interaction.response.send_message(
            "A configured onboarding role is missing. Ask the bot owner to check the setup.", ephemeral=True
        )
        return

    other_onboarding_roles = [role for role in [visitor, *tryout_roles] if role is not None and role != selected_role]
    await applicant.remove_roles(*other_onboarding_roles, reason="Applicant onboarding status changed")
    await applicant.add_roles(selected_role, reason="Applicant onboarding status assigned")
    save_ticket(interaction.guild.id, applicant.id, interaction.channel.id, role_name)
    await interaction.response.send_message(
        f"Assigned **{role_name}** to {applicant.mention}. This ticket will close in 5 seconds.",
        ephemeral=True,
    )
    await interaction.channel.send(
        f"{applicant.mention} was assigned **{role_name}** by {interaction.user.mention}. "
        "This ticket will close in 5 seconds."
    )
    await asyncio.sleep(5)
    await interaction.channel.delete(reason=f"Onboarding completed: {role_name} assigned")
    remove_ticket(interaction.guild.id, applicant.id)


def safe_channel_name(display_name: str, user_id: int) -> str:
    normalized = unicodedata.normalize("NFKD", display_name).encode("ascii", "ignore").decode().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")[:75].strip("-")
    return f"app-{slug or 'member'}-{str(user_id)[-4:]}"


def applicant_embed(member: discord.Member) -> discord.Embed:
    embed = discord.Embed(
        title="Welcome to Rionnag",
        description=(
            "If you're visiting, press **I'm a visitor** to get the Visitor role right away.\n"
            "If you'd like to try out for a game, press its button below and fill in the short form. "
            "That will notify the manager for that game. They can approve your game-specific tryout role after reviewing it.\n\n"
            "A tryout role is not team membership; actual team roles are assigned separately after a tryout. "
            "Managers are not notified just because someone joined."
        ),
        color=DEFAULT_EMBED_COLOR,
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text=f"User ID: {member.id}")
    return embed


async def refresh_ticket_message(channel: discord.TextChannel, member: discord.Member) -> None:
    embed = applicant_embed(member)
    embed_message: discord.Message | None = None
    async for message in channel.history(limit=30):
        if message.author.id == bot.user.id and message.embeds:
            if message.embeds[0].footer.text == f"User ID: {member.id}" or (
                message.embeds[0].title and "New member:" in message.embeds[0].title
            ):
                try:
                    await message.edit(
                        content=member.mention,
                        embed=embed,
                        view=ApplicantActions(),
                        allowed_mentions=discord.AllowedMentions(users=[member], roles=False, everyone=False),
                    )
                    embed_message = message
                    break
                except discord.HTTPException:
                    logger.exception("Couldn't refresh onboarding controls in channel %s; retrying without custom emoji", channel.id)
                    global CUSTOM_EMOJI_ENABLED
                    CUSTOM_EMOJI_ENABLED = False
                    await message.edit(
                        content=member.mention,
                        embed=applicant_embed(member),
                        view=ApplicantActions(),
                        allowed_mentions=discord.AllowedMentions(users=[member], roles=False, everyone=False),
                    )
                    embed_message = message
                    break
    if embed_message is None:
        await send_applicant_message(channel, member)
        return

    # Consolidate any older standalone ping into the embed message's normal content.
    async for message in channel.history(limit=30):
        if message.author.id == bot.user.id and message.id != embed_message.id and message.content == member.mention:
            await message.delete()


async def send_applicant_message(channel: discord.TextChannel, member: discord.Member) -> None:
    global CUSTOM_EMOJI_ENABLED
    try:
        await channel.send(
            content=member.mention,
            embed=applicant_embed(member),
            view=ApplicantActions(),
            allowed_mentions=discord.AllowedMentions(users=[member], roles=False, everyone=False),
        )
    except discord.HTTPException:
        logger.exception("Couldn't send onboarding embed/buttons to channel %s; retrying without custom emoji", channel.id)
        CUSTOM_EMOJI_ENABLED = False
        await channel.send(
            content=member.mention,
            embed=applicant_embed(member),
            view=ApplicantActions(),
            allowed_mentions=discord.AllowedMentions(users=[member], roles=False, everyone=False),
        )


async def create_applicant_channel(member: discord.Member) -> None:
    key = (member.guild.id, member.id)
    lock = ticket_creation_locks.setdefault(key, asyncio.Lock())
    async with lock:
        await _create_applicant_channel(member)


async def _create_applicant_channel(member: discord.Member) -> None:
    guild = member.guild
    existing = ticket_for(guild.id, member.id)
    if existing:
        existing_channel = guild.get_channel(existing[0])
        if isinstance(existing_channel, discord.TextChannel):
            return
        remove_ticket(guild.id, member.id)
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        member: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
    }
    manager = role_named(guild, MANAGER_ROLE_NAME)
    if manager is None:
        logger.error("Missing manager role %r in guild %s", MANAGER_ROLE_NAME, guild.id)
        return
    overwrites[manager] = discord.PermissionOverwrite(
        view_channel=True,
        send_messages=True,
        read_message_history=True,
        manage_messages=True,
    )
    owner = guild.get_member(guild.owner_id)
    if owner is None:
        try:
            owner = await guild.fetch_member(guild.owner_id)
        except discord.HTTPException:
            logger.exception("Couldn't fetch server owner %s", guild.owner_id)
    if owner is not None:
        overwrites[owner] = discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True, manage_messages=True
        )

    channel = await guild.create_text_channel(
        safe_channel_name(member.display_name, member.id),
        overwrites=overwrites,
        reason=f"Private onboarding for {member} ({member.id})",
    )
    save_ticket(guild.id, member.id, channel.id)
    await send_applicant_message(channel, member)
    logger.info("Created applicant channel %s for %s", channel.id, member.id)


async def restore_ticket_state(previous_start: float | None) -> None:
    if not GUILD_ID:
        return
    guild = bot.get_guild(GUILD_ID)
    if guild is None:
        logger.error("Configured guild %s is not available to the bot", GUILD_ID)
        return

    # Keep the DB's ticket-to-channel links in sync with Discord after restarts.
    for member_id, channel_id in list_tickets(guild.id):
        channel = guild.get_channel(channel_id)
        if channel is None:
            remove_ticket(guild.id, member_id)
            continue
        member = guild.get_member(member_id)
        if member is None:
            try:
                member = await guild.fetch_member(member_id)
            except discord.NotFound:
                if isinstance(channel, discord.TextChannel):
                    try:
                        await channel.delete(reason="Applicant left while the bot was offline")
                    except discord.HTTPException:
                        logger.exception("Couldn't delete stale applicant channel %s", channel.id)
                        continue
                remove_ticket(guild.id, member_id)
                continue
            except discord.HTTPException:
                logger.exception("Couldn't fetch applicant %s while restoring ticket", member_id)
                continue
        if isinstance(channel, discord.TextChannel):
            await refresh_ticket_message(channel, member)

    if previous_start is None:
        logger.info("Initialized onboarding state; existing members won't be backfilled on first start")
        return

    # Discord doesn't replay gateway join events sent while the bot was offline.
    # Member join timestamps let us create tickets for those who are still in the guild.
    async for member in guild.fetch_members(limit=None):
        joined_at = member.joined_at.timestamp() if member.joined_at else 0
        if joined_at > previous_start and ticket_for(guild.id, member.id) is None:
            await create_applicant_channel(member)


@bot.event
async def setup_hook() -> None:
    initialize_state()
    bot.add_view(ApplicantActions())
    for game in GAMES:
        bot.add_view(ReviewActions(game))


@bot.event
async def on_ready() -> None:
    global initialized
    if initialized:
        return
    if GUILD_ID:
        guild_object = discord.Object(id=GUILD_ID)
        bot.tree.copy_global_to(guild=guild_object)
        await bot.tree.sync(guild=guild_object)
    else:
        await bot.tree.sync()
    previous_start = initialize_state()
    await restore_ticket_state(previous_start)
    mark_started()
    initialized = True
    logger.info("Ready as %s", bot.user)


@bot.event
async def on_member_join(member: discord.Member) -> None:
    if GUILD_ID and member.guild.id != GUILD_ID:
        return
    try:
        await create_applicant_channel(member)
    except discord.Forbidden:
        logger.exception("Missing permissions while onboarding %s", member.id)
    except discord.HTTPException:
        logger.exception("Discord error while onboarding %s", member.id)


@bot.event
async def on_member_remove(member: discord.Member) -> None:
    ticket = ticket_for(member.guild.id, member.id)
    if not ticket:
        return
    channel = member.guild.get_channel(ticket[0])
    if isinstance(channel, discord.TextChannel):
        try:
            await channel.delete(reason="Applicant left the server")
        except discord.HTTPException:
            logger.exception("Couldn't delete applicant channel %s after member left", channel.id)
            return
    remove_ticket(member.guild.id, member.id)


@bot.tree.command(name="ping", description="Check whether the bot is responsive")
async def ping(interaction: discord.Interaction) -> None:
    await interaction.response.send_message(f"Pong! {round(bot.latency * 1000)} ms", ephemeral=True)


@bot.tree.command(name="onboard", description="Create a private onboarding channel for a member")
@discord.app_commands.describe(member="The member to create an onboarding channel for")
async def onboard(interaction: discord.Interaction, member: discord.Member) -> None:
    if not interaction.guild or not can_manage_applicant(interaction):
        await interaction.response.send_message("Only the server owner and Recruitment Managers can use this.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    await create_applicant_channel(member)
    await interaction.followup.send(f"Created a private onboarding channel for {member.mention}.", ephemeral=True)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    bot.run(TOKEN, log_handler=None)
