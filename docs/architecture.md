# Shared bot architecture

One Discord identity and gateway connection loads independent server modules through
`rionnag/servers/registry.py`. The existing installed Windows supervisor still owns the
single bot process and its separate Rionnag scrim collector. No new service is required.

| Server | Guild | Module | Private data |
| --- | --- | --- | --- |
| RIONNAG eSports | 1554260744327929987 | rionnag/servers/rionnag | Existing data/rionnag.sqlite3 and scrim databases |
| The Third Eye | 1549838808680374293 | rionnag/servers/third_eye | data/servers/third_eye/locks.sqlite3 |

`app.py` builds the intents required by the enabled profiles, loads their setup functions,
and syncs each guild's command registry separately. Every module registers cogs with an
explicit guild. There are no global feature commands and global fallback is disabled.
The command tree rejects unknown guilds, DMs and commands belonging to another guild,
including nested commands. Guild-join sync runs only for a configured server.

Joining an unconfigured server does not enable any features there. Each event listener
must guard its guild independently; application-command checks do not protect gateway events
or persistent UI callbacks. Server-specific UI and workflow authorization remain in their
own domain. Private DM interactions for Rionnag scrims retain their existing checks.

Rionnag's composition moved to servers/rionnag; its established domain imports, IDs, forms,
database paths and workflow behavior remain stable. Root cogs/services/ui/scrims are legacy
**Rionnag-only** code. Third Eye code is fully contained in its server package. See
[Rionnag architecture](servers/rionnag/architecture.md) and
[The Third Eye](servers/third_eye/README.md) for their independent contracts.

## Adding a server

1. Create `rionnag/servers/<key>/` with `setup(bot, profile)` and a scoped AGENTS.md.
2. Add `ServerProfile(key, guild_id, module, message_content=...)` to the registry.
3. Register cogs using `bot.add_cog(cog, guild=discord.Object(id=profile.guild_id))`.
   Keep events/UI gated to that guild and use profile.data_dir for private persistence.
4. Add server-specific docs under docs/servers/<key>/ and tests proving isolation.
5. Test, invite the bot with the needed permissions and restart the existing service.

Do not introduce a second token, client, service or shared feature database for each server.
