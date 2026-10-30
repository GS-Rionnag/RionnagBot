# Shared Windows service operations

The canonical checkout is RionnagBot. Its already-installed per-user Windows supervisor
runs one bot connection for all server profiles and the existing Rionnag scrim collector.
It starts hidden at Windows sign-in and restarts crashed children after fifteen seconds.
The PC must stay awake and online. No terminal or chat needs to stay open.

```powershell
rtk proxy .venv\Scripts\python.exe -m rionnag.service status
rtk proxy .venv\Scripts\python.exe -m rionnag.service restart
rtk proxy .venv\Scripts\python.exe -m rionnag.service stop
rtk proxy .venv\Scripts\python.exe -m rionnag.service start
```

All controls apply to the canonical supervisor; do not launch worktree bots or a standalone
`trollbot` client. `install` repairs/enables the existing hidden sign-in startup entry. Status
reports process health; successful guild-command sync and a fresh gateway connection in the
private bot log establish Discord readiness. Never print raw logs or credentials to public output.

Install project dependencies with `rtk proxy .venv\Scripts\python.exe -m pip install -e ".[dev]"`.
Server Members and Message Content intents must be enabled for the application (already enabled).
Adding a server means adding its package/registry entry, testing and restarting this service.
The collector keeps its separate environment because discord.py-self conflicts with discord.py.

## Migrating The Third Eye from the retired checkout

1. Stop the old process with `../trollbot/.venv/Scripts/python.exe -m trollbot stop` before
   retiring its launcher. Do not run old and new message handlers simultaneously.
2. Run `.venv/Scripts/python.exe -m scripts.migrate_third_eye` from this checkout.
   It backs up the old SQLite database into data/servers/third_eye/locks.sqlite3 once,
   preserving locked users without exposing IDs. Existing destinations are never overwritten,
   so rerunning cannot restore an old lock that was since turned off.
3. Restart this installed service. Check the log for 12 Rionnag commands and one Third Eye
   command synced to their own servers, followed by the gateway connection.

The retired trollbot source/data remain available as an archive; it is not a dependency.
No standalone startup entry is required. Logs/state remain ignored by Git.

[Rionnag operations](servers/rionnag/operations.md) contain only that server's forms, role
repair, scrim workflows and existing GitHub change notifications.
[Third Eye operations](servers/third_eye/README.md) describe its uwu behavior and permissions.
