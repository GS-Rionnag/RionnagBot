# Architecture

RionnagBot composes Store, Applications, and feature cogs. Onboarding handles joins, repairs, reloads, and persistent view registration. Profiles owns stats/promotion. Scrims adapts the retained scrim controller to the new profile store.

The scrim cog passes `Store.saved_uid` to the controller so accepted account identities come from the current member/profile registry, without querying the removed legacy account-claims table. Saved scrim channel mappings and lobby message IDs reconnect voice events to the existing dashboard; startup queues members already in waiting and edits that dashboard in place.

The monitor probes only real players in the starting six and skips simulated accounts and substitutes. Test rerolls may bench the host; an all-simulated starting lineup correctly stays waiting even when a real substitute enters a Custom game.

The member registry is authoritative: roles alone do not prove completion. Members without completed data receive tickets during startup, joins, or repair, including the owner. Reconciliation retries every five minutes. Per-member locks serialize transitions; an OS lock prevents duplicate processes.

Leaving the server erases the member registry entry, all form answers and embedded stats caches, role-restoration metadata, pending DMs, saved game profiles, and availability. The private ticket is deleted. Reconciliation also erases entries for members who left while the bot was offline. Rejoining starts fresh onboarding with no saved answers or automatic restoration.

States: new → visitor, or new/visitor/rejected → pending → deciding → accepted/rejected. Changed forms move outdated entries to reset. Accepted members cache assignable roles and return to their former completed status after filling the current form. Draft/rejected answers never authorize automatic approval.

All entrypoints use Applications.submit. Onboarding already has a private channel; visitors get one at submission. There is no user close/cancel action. The review panel has Accept, Reject, and an applicant-only Edit button. Managers cannot review themselves; the server owner has an explicit review override for any game.

Pending applicants can reopen prefilled forms through the review panel's Edit button. Editing drafts stay in the ephemeral form session; the stored application remains unchanged until validated resubmission updates its original message. This button only edits pending applications, and a decision during editing invalidates the draft. Edits preserve pending status, manager access, and roles.

Anyone can use `/edit_form` to edit their own saved current-version game data from any server channel. Accepted members update their saved profile without a ticket, review, or role changes; pending members update the existing application. Rejected/visitor records retain their status and do not gain acceptance. Drafts stay ephemeral until complete validation and account verification. A status change while editing invalidates the draft. Members undergoing a reset must finish the onboarding form first.

Reset returners with saved acceptance are automatically restored without posting a review panel. The owner also receives automatic acceptance on fresh form completion after the initial wipe; they are not asked to approve themselves.

SQLite stores status, game/version, JSON answers, channel/message IDs, restoration intent, and pending DMs. Reset snapshots are written before role removal and survive interrupted resets. Decisions first enter deciding so interrupted role changes can retry. Ticket topics identify members for recovery if Discord creation succeeds before SQLite saves. Failed DMs remain queued and results appear in tickets before deletion.

Stable question keys preserve answers. UI collects four questions per modal, with a username search note at the top, persists each page, and continues through an ephemeral button. Marvel Rivals names are search queries: applicants choose the actual account from paginated results. Availability uses a day selector and start/end time selectors; applicants add/edit/remove days, then press Finish & submit. Structured days and a readable summary are cached. Reopening prefills saved answers and schedules. Stale forms cannot submit after version changes. Validation and public-account verification precede submission.

Account uniqueness applies to pending applications, accepted members, decisions in progress, and reset members with saved role restoration. Rejected/visitor answers and unfinished drafts are cached for prefilling but do not reserve the game account. Duplicate-account errors mention the member holding the active link.

Start/end selection happens sequentially on the message, allowing end choices to update immediately. End times start one hour after the chosen start and continue through midnight. Older cached overnight windows still display. Unfinished version-zero drafts do not trigger version resets during background repair, and actual resets retain the existing welcome message ID.

Entering day selection includes https://youtu.be/TQXOmacHAYs in the ephemeral message content, both after account selection and when entering directly by UID. Form edits use the same entry point.

Welcome and tryout panels are embeds with the MR game emoji. Applicant welcome messages mention the member in the message content above the embed, with explicit allowed mentions so new messages ping them. Existing panels are edited in place. Ticket names use the Discord username plus the last four digits of the member ID; the immutable topic/full ID and database mapping remain authoritative for ownership/recovery. These presentation changes do not change the form definition/version or force an unrelated reset.

player_profiles is populated only after acceptance/restoration and removed during reset/rejection. Existing scrim tables share the private connection; collected offers use a separate private feed database. The collector needs a separate environment because discord.py-self conflicts with discord.py.

Application messages contain the form embed and a separate Marvel Rivals player-data embed, without a text attachment. Successful provider results are cached against the selected account UID; lookup failures leave the submitted application available for review. Availability summaries in both the editor and application use Discord time-only timestamps (`<t:UNIX:t>`) calculated from the selected time zone and upcoming weekday, so Discord renders each viewer's local time.

Player lookup uses rivals-api 5.0's canonical `player.stats.win_rate(season="current")`, `hero_win_rates(season="current")`, and `class_win_rates(season="current")`. All three embed fields are labeled Current Season. Mode is omitted, preserving the SDK default. The SDK verifies the global current season. Omitted method uses its normal summary calculation; `/lookup method:Precise` applies precise history verification to all three rates. Application stats use the default normal method. Failures remain retryable; no all-season fallback is substituted. Hero leaderboard placements remain separate metadata from `summary_heroes` for both modes, displayed after hero win rates when available. Cache versions refresh embeds without changing form versions, answers, or roles. Empty/partial results remain retryable.
