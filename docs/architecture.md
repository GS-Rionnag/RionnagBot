# Architecture

RionnagBot composes Store, Applications, and feature cogs. Onboarding handles joins, repairs, reloads, and persistent view registration. Profiles owns stats/promotion. Scrims adapts the retained scrim controller to the new profile store.

The scrim cog passes `Store.saved_uid` to the controller so accepted account identities come from the current member/profile registry, without querying the removed legacy account-claims table. Saved scrim channel mappings and lobby message IDs reconnect voice events to the existing dashboard; startup queues members already in waiting and edits that dashboard in place.

The member registry is authoritative: roles alone do not prove completion. Members without completed data receive tickets during startup, joins, or repair, including the owner. Reconciliation retries every five minutes. Per-member locks serialize transitions; an OS lock prevents duplicate processes.

States: new → visitor, or new/visitor/rejected → pending → deciding → accepted/rejected. Changed forms move outdated entries to reset. Accepted members cache assignable roles and return to their former completed status after filling the current form. Draft/rejected answers never authorize automatic approval.

All entrypoints use Applications.submit. Onboarding already has a private channel; visitors get one at submission. There is no user close/cancel action. The review panel has Accept, Reject, and an applicant-only Edit button. Managers cannot review themselves; the server owner has an explicit review override for any game.

Pending applicants can reopen prefilled forms through Edit. Editing drafts stay in the ephemeral form session; the stored application remains unchanged until validated resubmission updates its original message. Accepted/rejected applications cannot be edited, and a decision during editing invalidates the draft. Edits preserve pending status, manager access, and roles.

Reset returners with saved acceptance are automatically restored without posting a review panel. The owner also receives automatic acceptance on fresh form completion after the initial wipe; they are not asked to approve themselves.

SQLite stores status, game/version, JSON answers, channel/message IDs, restoration intent, and pending DMs. Reset snapshots are written before role removal and survive interrupted resets. Decisions first enter deciding so interrupted role changes can retry. Ticket topics identify members for recovery if Discord creation succeeds before SQLite saves. Failed DMs remain queued and results appear in tickets before deletion.

Stable question keys preserve answers. UI collects four questions per modal, with a username search note at the top, persists each page, and continues through an ephemeral button. Marvel Rivals names are search queries: applicants choose the actual account from paginated results. Availability uses a day selector and start/end time selectors; applicants add/edit/remove days, then press Finish & submit. Structured days and a readable summary are cached. Reopening prefills saved answers and schedules. Stale forms cannot submit after version changes. Validation and public-account verification precede submission.

Start/end selection happens sequentially on the message, allowing end choices to update immediately. End times start one hour after the chosen start and continue through midnight. Older cached overnight windows still display. Unfinished version-zero drafts do not trigger version resets during background repair, and actual resets retain the existing welcome message ID.

Welcome and tryout panels are embeds with the MR game emoji. Applicant welcome messages mention the member in the message content above the embed, with explicit allowed mentions so new messages ping them. Existing panels are edited in place. Ticket names use the Discord username plus the last four digits of the member ID; the immutable topic/full ID and database mapping remain authoritative for ownership/recovery. These presentation changes do not change the form definition/version or force an unrelated reset.

player_profiles is populated only after acceptance/restoration and removed during reset/rejection. Existing scrim tables share the private connection; collected offers use a separate private feed database. The collector needs a separate environment because discord.py-self conflicts with discord.py.

Application messages contain the form embed and a separate Marvel Rivals player-data embed, without a text attachment. Successful provider results are cached against the selected account UID; lookup failures leave the submitted application available for review. Availability summaries in both the editor and application use Discord time-only timestamps (`<t:UNIX:t>`) calculated from the selected time zone and upcoming weekday, so Discord renders each viewer's local time.

Player lookup first seeds all available match history with `fetch_win_rate(method="exact")`. Overall season, all-season hero, and all-season class calculations then use `method="cached"`. If history has no usable season rate, the documented `player.win_rate` supplies the profile's competitive season rate. Hero leaderboard positions come from `player.stats.heroes(mode="competitive", season="all")` and appear after each available hero win rate as `#324`. Stats embed cache versions refresh repaired displays without changing form versions, answers, or roles.

Accounts without Competitive hero history fall back to Quick Play hero/class calculations from that same cache. Both fields explicitly say Quick Play and hero placements use that mode's public hero stats. Empty or partial results are not saved as permanent stats embeds, allowing later reconciliation to retry.
