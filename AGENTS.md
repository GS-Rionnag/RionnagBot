# Shared bot agent instructions

This checkout is a multi-purpose Discord bot serving explicitly configured servers.
Do not apply RIONNAG eSports' organization rules to another server.

Read `C:\Users\giris\.codex\RTK.md`; prefix shell commands with `rtk`.
Read README.md and docs/architecture.md, docs/operations.md and docs/permissions.md
before changing shared behavior. Use the canonical per-user Windows supervisor:
`rtk proxy .venv\Scripts\python.exe -m rionnag.service status|restart|stop|start`.
Do not start a second bot connection, worktree bot or standalone trollbot process.
Never kill Python processes by name.

## Scope and ownership

- `rionnag/servers/registry.py` declares the configured servers and their setup modules.
- `rionnag/servers/rionnag/` and `docs/servers/rionnag/` concern **RIONNAG eSports only**.
  Read its scoped AGENTS.md before changing organization behavior. The legacy root
  `rionnag/cogs`, `services`, `ui`, `scrims`, `storage.py`, `config.py` and `config/forms.json`
  are also Rionnag-only domain code, retained at stable import/data paths for compatibility.
- `rionnag/servers/third_eye/` and `docs/servers/third_eye/` concern **The Third Eye only**.
- New server features belong in their own `rionnag/servers/<key>/` package, with their own
  config, cogs/services/storage and scoped docs/agent instructions. Do not reuse Rionnag role IDs,
  forms, membership policies or databases as generic defaults.
- Shared composition, command routing and process control belong in app.py and service.py.
  Commands must register directly to their server; never publish global feature commands.
  Event listeners must check their server before reading or mutating member/message state.

Keep private state under ignored data/servers/<key>/ for new server modules. Existing
Rionnag databases stay at their established paths. Do not read/write another server's
private data as part of a feature. Never expose credentials, webhook URLs, member answers,
logs or databases in output or public commits. Prior wipe authorization was limited to the
historical Rionnag rebuild; it grants no permission for future wipes in any server.

Run meaningful affected regressions and server-isolation tests. Use temporary test data.
Commit every coherent change after checks, maintain CHANGELOG.md and push completed changes
to the configured public remote. Inspect staged paths/diffs for private data before pushing.
Do not rewrite unrelated user changes. Discord connector actions use the connected user's
account; do not infer that it has access to every configured server.
