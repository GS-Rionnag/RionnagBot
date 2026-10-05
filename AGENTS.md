# Agent instructions

Use the shared per-user supervisor in this canonical checkout for runtime control: `rtk proxy .venv\Scripts\python.exe -m rionnag.service status|restart|stop|start` (choose one action). Do not launch a bot per chat/worktree or kill Python processes by name. Read docs/operations.md for startup and recovery details.

Read `C:\Users\giris\.codex\RTK.md` and prefix shell commands with `rtk`.

Read README.md, docs/architecture.md, docs/permissions.md, and docs/operations.md before modifying behavior. This is the Rionnag owner's private server bot. Discord connector tools act as the owner account; prefer them for server inspection and the bot for ongoing workflows. Initial data deletion and membership rework were explicitly authorized. Do not infer authorization for future wipes.

Keep composition in app.py, commands/events in cogs, workflows in services, UI in ui, persistence in storage.py. Forms live in config/forms.json. Preserve stable question keys; increment versions whenever definitions change. Enforce selected-manager review with the explicit server-owner override: the owner can accept/reject anyone including themselves. Never bypass completion or role restoration. Rionnag is the only non-managed role exempt from resets, including for the owner. Discord owners/admins necessarily bypass private-channel overwrites.

Preserve scrim behavior when changing onboarding. Run meaningful affected regression tests. Test data belongs in temporary directories. Never expose secrets or private answers in output or public commits.

Commit **every coherent minor or major change**, after appropriate checks, with a descriptive message. Related code/tests/docs may share a commit; follow-up repairs must be separate commits. Maintain CHANGELOG.md for behavior/operational changes. Push completed changes to the configured public remote unless instructed otherwise. Inspect staged paths/diffs for private data before pushing. Do not rewrite unrelated user changes.
