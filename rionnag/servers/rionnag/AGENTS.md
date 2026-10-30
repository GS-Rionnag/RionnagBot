# RIONNAG eSports only

Scope: guild 1554260744327929987. These instructions describe the organization's existing
onboarding, membership and scrim domain. They do not apply to The Third Eye or future servers.
The legacy root cogs/services/ui/scrims/storage/config modules implement this domain too.

Read docs/servers/rionnag/architecture.md, permissions.md and operations.md before modifying
this server's behavior. Keep composition here, commands/events in cogs, workflows in services,
UI in ui, and persistence in storage.py. Forms live in config/forms.json. Preserve stable
question keys and increase versions when definitions change.

Enforce selected-manager review and the explicit server-owner override: the owner can
accept/reject anyone, including themselves. Never bypass completion or role restoration.
The Rionnag role is the only non-managed role exempt from resets, including for the owner.
Discord owners/admins necessarily bypass private-channel overwrites. Preserve scrim behavior
when changing onboarding. Historical initial membership/data reset permission does not
authorize future wipes. Run meaningful affected regression tests with temporary data.
