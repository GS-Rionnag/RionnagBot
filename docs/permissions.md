# Shared access boundaries

This is a multi-purpose bot. Server policies are scoped by registry entry, not by the
Discord identity shared across servers. A server owner's role does not grant privileges
in another server. Never use Rionnag role IDs, resets or applicant rules as global policy.

- [RIONNAG eSports permissions](servers/rionnag/permissions.md): organization-specific roles,
  onboarding, review and scrim access; applies only to guild 1554260744327929987.
- [The Third Eye permissions](servers/third_eye/README.md): moderator-only uwu locks and
  message/webhook access; applies only to guild 1549838808680374293.

Commands are guild-registered and runtime-checked against that guild's command objects.
Gateway listeners and persistent views require their own domain authorization. Unconfigured
servers receive no feature commands or automatic processing. Grant only the permissions
needed by a server's enabled features; adding a profile does not grant Discord permissions.
