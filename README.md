# RionnagBot: shared Discord bot

One bot identity, one installed Windows service and independent features for explicitly
configured servers. Public source contains no credentials, member answers or runtime data.

| Server | Features | Documentation |
| --- | --- | --- |
| RIONNAG eSports | Onboarding, applications, profiles and scrims | [Rionnag only](docs/servers/rionnag/architecture.md) |
| The Third Eye | `/uwuify member` webhook lock toggle | [Third Eye only](docs/servers/third_eye/README.md) |

Organization-specific roles, forms and reset rules apply only to Rionnag. Other servers
have their own packages, data and policies. Unconfigured servers have no enabled features.

Read [shared architecture](docs/architecture.md), [service operations](docs/operations.md),
[access boundaries](docs/permissions.md) and [agent instructions](AGENTS.md).

## Run and verify

```powershell
rtk proxy .venv\Scripts\python.exe -m pip install -e ".[dev]"
rtk proxy .venv\Scripts\python.exe -m rionnag.service status
rtk proxy .venv\Scripts\python.exe -m rionnag.service restart
rtk proxy .venv\Scripts\python.exe -m unittest discover -s tests -q
rtk proxy .venv\Scripts\ruff.exe check .
```

The existing hidden per-user service starts at Windows sign-in and restarts crashed bot
and collector processes. Both servers share its single bot connection. Keep the PC awake
and connected; no chat or terminal must remain open. See operations for installation and recovery.

## Workspace

| Path | Scope |
| --- | --- |
| rionnag/app.py, service.py | Shared client, guild routing and installed supervisor |
| rionnag/servers/registry.py | Server list, modules and intent requirements |
| rionnag/servers/rionnag/ | Rionnag composition and scoped agent instructions |
| rionnag/servers/third_eye/ | Third Eye cogs, config, text transformation and persistence |
| rionnag/cogs, services, ui, scrims, storage.py, config.py | Legacy Rionnag domain only; stable imports/data paths |
| config/forms.json | Rionnag game forms only |
| docs/servers/<key>/ | Server-specific contracts |
| scrim_collector/ | Rionnag collector, separate dependency environment |
| tests/ | Regression, routing and migration coverage |
| data/servers/<key>/ | Ignored private state for new server modules |
| data/rionnag.sqlite3, data/scrim_feed.sqlite3 | Existing private Rionnag state |

To add another purpose/server, add its package and registry profile as described in the
shared architecture guide. Do not start another bot process or copy Rionnag's server rules.
