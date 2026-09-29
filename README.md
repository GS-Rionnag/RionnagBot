# RionnagBot

RionnagBot is a Discord onboarding bot for private member tickets, visitor self-selection, and game tryout applications. It stores ticket state in SQLite and restores active tickets after a restart.

## Requirements

- Python 3.11 or newer
- A Discord application and bot token
- A Discord server where the bot can manage channels and roles

## Local setup

1. Create and activate a virtual environment:

   ```powershell
   py -3.11 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install -e .[dev]
   ```

2. Copy `.env.example` to `.env` and fill in the required values.
3. Enable **Server Members Intent** in the Discord Developer Portal. Grant the bot Manage Channels, Manage Roles, View Channels, Send Messages, and Read Message History. Keep the bot's role above onboarding roles.
4. Create `Recruitment Manager`, `Visitor`, and for each configured game, `<Game> Manager` and `<Game> Tryout` roles. The server owner and Recruitment Managers can manage every ticket; a game's managers can review that game's applications.
5. Start the bot:

   ```powershell
   python bot.py
   ```

The bot syncs `/ping` and `/onboard` to `GUILD_ID` for quick updates. Without `GUILD_ID`, commands are synced globally. New members receive a private application channel. Managers are notified only after a member submits a game application. Approval assigns a tryout role; it never assigns team membership.

## Configuration

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `DISCORD_TOKEN` | Yes | — | Bot token; keep it secret |
| `GUILD_ID` | No | `0` | Development server for guild-scoped commands |
| `ONBOARDING_DB_PATH` | No | `onboarding.sqlite3` | SQLite ticket registry path |
| `MANAGER_ROLE_NAME` | No | `Recruitment Manager` | Role allowed to manage all tickets |
| `VISITOR_ROLE_NAME` | No | `Visitor` | Self-selected visitor role |
| `GAMES` | No | `Marvel Rivals` | Comma-separated game names |
| `MARVEL_EMOJI_ID` | No | `0` | Optional custom emoji ID for buttons |
| `MARVEL_EMOJI_NAME` | No | `MR` | Name used with the custom emoji ID |

For each name in `GAMES`, create matching `<Game> Manager` and `<Game> Tryout` roles. Discord buttons support up to 20 configured games.

## Project layout

```text
.
├── bot.py                 # Runtime entry point
├── setup_discord_icons.py # Optional one-time role/emoji setup utility
├── onboarding.sqlite3     # Local runtime state (created automatically)
├── .env.example           # Configuration template; copy to .env
├── pyproject.toml         # Package metadata and developer tool settings
└── requirements.txt       # Compatibility install requirements
```

`bot.py` currently owns Discord interactions, onboarding workflows, and SQLite persistence. Keep user-facing flows in Discord views/commands, persistence behind small database functions, and configuration in environment-backed settings as the codebase is split into modules. Avoid changing ticket status, role assignment, permissions, or recovery behavior without updating the corresponding documentation and adding focused coverage.

## State and deployment

The SQLite file contains member-to-channel links and application status. Keep it on durable storage and back it up before moving or redeploying the bot. The bot reconciles stored channels on startup and creates tickets for members who joined while it was offline. Do not commit `.env`, database files, virtual environments, or logs.

`setup_discord_icons.py` is optional. It expects `assets/marvel_rivals_logo.png` and a configured `GUILD_ID`; the normal bot does not require that asset.
