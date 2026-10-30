# The Third Eye

Scope: guild `1549838808680374293` only. This server uses entertainment commands, with
no Rionnag onboarding, membership roles, application forms, resets or scrim policies.

`/uwuify member` toggles a persistent uwu lock; running it again unlocks the member.
The invoker needs Manage Messages. The bot needs View Channel, Send Messages,
Manage Messages and Manage Webhooks; threads also need Send Messages in Threads.
Missing permissions preserve original messages.

Locked members' new text messages are transformed by StarrFox/uwuify 1.3.1 (smileys,
yu substitutions and stuttering) and reposted through a bot-owned webhook using their
current display name and avatar. This is not a verified copy of uwuify.uk's private algorithm.
The original is deleted only after successful reposting; failed deletion rolls back the copy.
Mentions never ping. Attachments are reuploaded; long text is attached without truncation.
URL-only messages, bots, webhooks, polls, stickers and unsupported message/channel types
are left untouched. Messages with text alongside a URL still transform.

Only locked user IDs are stored in data/servers/third_eye/locks.sqlite3. Message bodies,
attachment URLs and webhook tokens are not persisted. Webhook tokens remain in memory.
See [shared operations](../../operations.md) for service controls and one-time migration.
The existing Windows service runs this module with Rionnag through one connection.
