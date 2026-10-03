# Architecture

RionnagBot composes Store, Applications, and feature cogs. Onboarding handles joins, repairs, reloads, and persistent view registration. Profiles owns stats/promotion. Scrims adapts the retained scrim controller to the new profile store.

The member registry is authoritative: roles alone do not prove completion. Members without completed data receive tickets during startup, joins, or repair, including the owner. Reconciliation retries every five minutes. Per-member locks serialize transitions; an OS lock prevents duplicate processes.

States: new → visitor, or new/visitor/rejected → pending → deciding → accepted/rejected. Changed forms move outdated entries to reset. Accepted members cache assignable roles and return to their former completed status after filling the current form. Draft/rejected answers never authorize automatic approval.

All entrypoints use Applications.submit. Onboarding already has a private channel; visitors get one at submission. There is no user close/cancel action. The review panel has exactly Accept and Reject. Managers cannot review themselves; the server owner has an explicit review override for any game.

Reset returners with saved acceptance are automatically restored without posting a review panel. The owner also receives automatic acceptance on fresh form completion after the initial wipe; they are not asked to approve themselves.

SQLite stores status, game/version, JSON answers, channel/message IDs, restoration intent, and pending DMs. Reset snapshots are written before role removal and survive interrupted resets. Decisions first enter deciding so interrupted role changes can retry. Ticket topics identify members for recovery if Discord creation succeeds before SQLite saves. Failed DMs remain queued and results appear in tickets before deletion.

Stable question keys preserve answers. UI collects four questions per modal, with a username search note at the top, persists each page, and continues through an ephemeral button. Marvel Rivals names are search queries: applicants choose the actual account from paginated results. Availability uses a day selector and start/end time selectors; applicants add/edit/remove days, then press Finish & submit. Structured days and a readable summary are cached. Reopening prefills saved answers and schedules. Stale forms cannot submit after version changes. Validation and public-account verification precede submission.

Welcome and tryout panels are embeds with the MR game emoji. Applicant welcome messages mention the member in the message content above the embed, with explicit allowed mentions so new messages ping them. Existing panels are edited in place. Ticket names use the Discord username plus the last four digits of the member ID; the immutable topic/full ID and database mapping remain authoritative for ownership/recovery. These presentation changes do not change the form definition/version or force an unrelated reset.

player_profiles is populated only after acceptance/restoration and removed during reset/rejection. Existing scrim tables share the private connection; collected offers use a separate private feed database. The collector needs a separate environment because discord.py-self conflicts with discord.py.
