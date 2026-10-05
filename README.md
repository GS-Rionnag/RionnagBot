# RionnagBot

A private Discord organization bot for **Rionnag**. The source is public; credentials, member answers, and runtime data are private. It serves exactly one server.

New members receive one private onboarding channel. They choose Visitor or complete a game application. Visitors can use Main → tryout to enter the same application flow. After submission, only the selected game's manager gains ticket access and Accept/Reject buttons. Acceptance grants the game tryout role; rejection grants Visitor. The bot sends the outcome by DM and deletes the completed ticket.

When a game form version increases, outdated members return to step one and lose assignable roles except Rionnag. Cached answers remain prefilled and editable; new required questions must be answered. Previously accepted players and managers regain their original roles automatically after completion. Pending/rejected applications still require review. The initial October 3, 2026 rebuild deliberately erased previous stored data and creates fresh onboarding for everyone, including the owner. Existing membership roles are captured from live Discord as fresh reset metadata; old stored answers are not retained.

Read [architecture](docs/architecture.md), [permissions](docs/permissions.md), [operations](docs/operations.md), and [agent instructions](AGENTS.md).

## Run and verify

```powershell
rtk proxy python -m venv .venv
rtk proxy .venv\Scripts\python.exe -m pip install -e ".[dev]"
rtk proxy .venv\Scripts\python.exe -m camoufox fetch
# Copy .env.example to .env and privately set DISCORD_TOKEN.
rtk proxy .venv\Scripts\python.exe -m rionnag.service install
rtk proxy .venv\Scripts\python.exe -m unittest discover -s tests -q
rtk proxy .venv\Scripts\ruff.exe check .
```

Enable Server Members Intent. Keep the bot role above membership roles and below Rionnag. An OS lock prevents duplicate bot instances.

On Windows, `install` starts the shared background supervisor and enables hidden startup at user sign-in without admin rights. Any chat can use `python -m rionnag.service status` or `restart` from this checkout. See operations for the full control commands. The PC must remain awake for the bot to stay online.

## Workspace

| Path | Purpose |
| --- | --- |
| config/forms.json | Games, stable question keys, form versions |
| rionnag/app.py | Composition and startup |
| rionnag/cogs/ | Feature commands and events |
| rionnag/services/ | Applications, resets, permission policy |
| rionnag/ui/ | Persistent buttons and paginated forms |
| rionnag/storage.py | Workflow/profile persistence |
| rionnag/integrations/ | Marvel Rivals provider and public stats |
| rionnag/scrims/ | Existing scrim subsystem |
| scrim_collector/ | Optional collector with separate environment |
| tools/ | Icon utilities |
| tests/, docs/ | Regression coverage and operating contract |
| data/, logs/ | Private generated state; ignored by Git |

Every future agent must commit each coherent minor or major change with a clear message. Never commit credentials, databases, backups, application transcripts, or logs.
