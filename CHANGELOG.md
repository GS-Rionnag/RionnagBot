# Change timeline

## 2026-10-08 - simplify owner scrim controls

- Remove No from the six-player owner panel. Hide Enter ranks & send after the advert is delivered,
  including refreshed panels and controls restored after restart; retain Bump post and Confirm scrim.
- Set the Host Scrims introduction footer to Use /edit_form to view more days; retain recovery of existing boards.

## 2026-10-08 - owner roster, advert bump, and official confirmation

- Six-player owner DMs list confirmed mentions grouped by saved best role in Tank / DPS / Support order.
  Add persistent owner-only Bump post and Confirm scrim buttons; refresh existing owner panels and restore
  management controls after sending an advert and after restart.
- Bump post queues one replacement at a time. The shared posting account revalidates the session, deletes
  only its recorded own advert, and reposts the same content. Persist delete/send phases and recover uncertain
  sends from history without blindly repeating them; atomically update the advert link on success.
- Confirm scrim marks that interval officially booked on the main board and owner DM. Persist the booking
  and stop new adverts, bumps, and player invitation DMs for that interval. Existing voice tracking is unchanged.

## 2026-10-08 - owner-entered advert rank range

- Replace the six-player approval's send button with Enter ranks & send. Open an owner-only rank form
  prefilled with Grandmaster and Celestial; submission revalidates readiness and queues a single-line
  LFS rank-range/timestamp advert. Do not fetch or publish individual players' current/peak ranks.
- Add a scoped HTTP-only utility for explicitly selected own advert edits, preserving their timestamp
  and synchronizing the delivered-content record after verification.

## 2026-10-08 - invite available players after three confirmations

- At three or more confirmations for an exact scrim interval, DM remaining available players with
  Discord full-date/start and end-time timestamps and persistent Yes/No buttons. Yes revalidates and
  confirms that player; No declines without changing other selections.
- Persist invitations and decisions per player/start/duration to prevent repeated DMs across refreshes
  and restarts. Recover interrupted deliveries from DM history; closed DMs do not loop indefinitely.

## 2026-10-08 - day chooser mention spacing

- Separate available-player mentions with spaces instead of commas in Choose a day.

## 2026-10-08 - remove top scrim card footers

- Remove footers from the three top scrim session embeds; the message labels identify their ranking.

## 2026-10-08 - top scrim message labels

- Rename the three public message labels to Top scrim option 1, 2, and 3.

## 2026-10-08 - top three separate scrim messages

- Keep the Choose a day / My selections header first and show only three ranked scrim messages below it.
  Remove the two previously created extra option messages, preserving confirmations and direct buttons.

## 2026-10-08 - concise chronological day chooser

- Choose a day shows only the five soonest qualifying days, ordered by date before confirmed and
  available attendance. Each day lists unconfirmed available-player mentions and their count.
- My selections retains every confirmed date so commitments beyond the shortened chooser can be withdrawn.

## 2026-10-08 - five scrim messages and direct joining

- Keep the HOST SCRIMS controls in their existing message and publish five separate ranked session
  messages, each with Join scrim and Withdraw buttons. Empty positions show disabled placeholders.
  Rank confirmed sessions by confirmed attendance then soonest date. Recover message identities and
  persistent buttons across restarts without reposting the board.
- Direct joins outside saved availability require a private Yes/Cancel warning. Store the player's
  explicit commitment for that exact interval and duration; preserve eligibility and best/secondary
  2–2–2 role checks. Withdrawals, departures, and invalidated votes clear schedule overrides.
- Owner approval and the outbound collector recognize explicit schedule commitments when validating
  the selected roster. No schedule edits, membership changes, or external adverts occur automatically.

## 2026-10-08 - available-player heading count

- Show the remaining unconfirmed availability count as **Available players** (number) on session cards.

## 2026-10-08 - exclude confirmations from available-player mentions

- Public session cards list only unconfirmed players under Available players. Confirmed players remain
  in their role lines; when everyone available has confirmed, display None remaining.

## 2026-10-07 - private confirmation picker refresh

- Refresh and close ephemeral scrim time pickers through the interaction webhook instead of the normal
  channel-message endpoint, fixing Unknown Message errors after confirmations and withdrawals save.

## 2026-10-07 - uncheck scrim confirmations

- Preselect confirmed times in Choose a day's time dropdown. Unchecking a time now withdraws that
  confirmation, including clearing every choice on the current page, without changing other pages.
- Refresh checkbox states after saving and close completed My selections withdrawals so stale selections
  cannot appear confirmed after removal. Validate additions before saving withdrawals together.

## 2026-10-07 - availability day save/remove timeout

- Acknowledge Save day and Remove a saved day before waiting for the member save lock. Refresh the
  private availability form through the acknowledged response, preserving draft and submission rules.

## 2026-10-07 - hosting selector interaction recovery

- Acknowledge day selection and return-to-days clicks before recalculating schedules, preventing slow
  calculations from missing Discord's interaction acknowledgement deadline.
- Time confirmations explicitly open a private processing response. Hosting controls log callback failures
  and send retry guidance rather than silently failing.

## 2026-10-07 - separate top-three scrim cards

- Keep HOST SCRIMS as an introduction/control embed and display First place, Second place, and Third place
  as separate embeds in the same persistent message. Rank all confirmed sessions by most confirmed players,
  then soonest start; counts do not need to tie. Show no more than three session cards.
- Use each scrim's Discord timestamp as its card title; place labels appear in the footer.

## 2026-10-07 - show only the best tied hosting sessions

- Public confirmed sessions show only the highest confirmed-player count, then highest available-player
  count. Show up to three equally ranked sessions, ordered soonest first; hide lower-ranked sessions.
- Replace the public available-player count with mentions of everyone available for that full interval.

## 2026-10-07 - simplify player hosting controls

- Remove View lineups from the hosting board; retain Choose a day and My selections.

## 2026-10-07 - confirmed players grouped by role

- Group public confirmed-player mentions under Tank, DPS, then Support using each player's saved best role.

## 2026-10-07 - confirmed-session board ordering

- Order public confirmed sessions by most confirmed players first, then soonest scrim start for ties.
  Private day/time recommendations retain their attendance and role-fit ranking.

## 2026-10-07 - owner DM approval when six players confirm

- Remove Host a session and Host settings from the public board and remove the hosting-settings command.
  Retain the player day/time chooser, withdrawals, and lineup viewer.
- When a specific interval first has six confirmed players forming a valid 2–2–2 team, DM the server owner
  its date/time with Yes, send advert and No buttons. Only the owner's Yes queues the anonymous roster advert;
  No sends nothing. Profile ranks are fetched after approval, using the existing paced rank-only lookups.
- Persist approval messages and decisions, restore buttons after restart, recover interrupted DM delivery,
  and suppress repeat prompts while a slot remains ready. Expired/replaced approvals cannot send; a slot
  that loses and regains readiness receives a fresh approval generation. No confirmation data is erased.

## 2026-10-07 - day-first hosting and attendance-first recommendations

- Keep the public hosting board free of suggested dates/times until a player confirms. Confirmed sessions
  display their exact interval, attendance counts, and member mentions with each player's saved best role.
- Replace the initial time list with a private day selector in the player's saved time zone. Show distinct
  confirmed/available player counts per day, then exact start/end windows and counts for the selected day.
- Rank days and times by confirmed players, then available players, then best-role fit. Keep the existing
  full-duration six-player, 2–2–2, best/secondary-only eligibility requirement and uncapped confirmations.
- Remove opponent min/max rank inputs and rank-range advert headers from hosting. Anonymous Player1–Player6
  current/peak rank lines remain. The independent finder rank filter is unchanged.

## 2026-10-07 - role-safe scrim hosting and anonymous roster adverts

- Add a persistent hosting board in channel 1557583583143526460 with private date-filtered confirmations,
  withdrawals, lineup inspection, and manager settings/publication previews. Suggest two-hour sessions
  over the next fourteen days, in thirty-minute increments, with configurable duration.
- Require six distinct current, accepted game members available for the full interval who can fill
  2 Tank / 2 DPS / 2 Support using only best/secondary roles. Prioritize confirmed teams, main-role fit,
  and confirmed substitutes. Never cap confirmations at six or include a worst-role-only composition.
- Publish explicitly confirmed LFS adverts through the shared owner-account collector. Include anonymous
  Player1–Player6 current/peak rank lines from linked profiles, without usernames, UIDs, mentions, or profile
  links. Missing ranks say Unavailable; publication always previews the exact anonymous text.
- Persist votes, selected rosters, board identity, and delivery status. Revalidate starters before sending,
  recover interrupted sends without blind retries, flag withdrawn starters, and allow reviewed replacement
  lineups without reposting. Session-length changes require fresh confirmations. Existing finder and live
  scrim tracking remain independent.

## 2026-10-07 - persistent single-starter scrim tracking

- Pin live and history checks to one real starter, preferring a currently selected player whose history previously verified a game. Save the selection across retries/restarts and use the recorded lineup for completed games.
- Fetch only that player's latest custom match, without federated history or the win-rate `exact` option. Verify its identity, timing, and recorded teammates against full match details.
- Retry unverified completed games without a time limit, including ended sessions. Keep provider cooldowns; retry other history errors after sixty seconds. Late verified logs still replace unverified summaries.

## 2026-10-07 - recover delayed scrim results and replace unverified summaries

- Keep completed games eligible for history verification for one hour after ending, including after a session closes. Non-rate-limit history errors rotate to another starter after fifteen seconds; provider cooldowns remain enforced.
- When late game data arrives, publish its verified game log, remove the previous session summary, and repost the updated summary beneath the game logs. Remove Unverified games when none remain.

## 2026-10-07 - mandatory manager force in/out selections

- Honor explicit incoming/outgoing pairs even when their current roles differ. Reassign the other five starters using saved secondary roles first, with the fewest off-role overrides when required to preserve 2 Tank / 2 DPS / 2 Support.
- Label off-role overrides in the confirmation and dashboard. Preserve the chosen players, counts, saved preferences, review confirmation, and running-game restrictions.

## 2026-10-07 - one scrim voice room with passive game tracking

- Keep the former waiting room as the sole Scrim voice channel and remove the extra main room. Preserve session/game data and restore bot-applied mutes during the one-time consolidation.
- Remove automatic voice admission, channel transfers, speaker overwrites, muting, and Sync voice controls. Everyone stays in the shared room and may speak, including Visitors and substitutes; Visitors still do not enter the team composition.
- Retain automatic Custom Room start/end detection, manual game/session controls, lineup management, and match statistics/results collection.

## 2026-10-07 - explicit scrim session end command

- Add manager-only `/scrim end` to close the entire session, restore voice state, preserve completed games, and return the dashboard to its waiting lobby. Any still-running game is marked unfinished without awarding completion.

## 2026-10-07 - end scrims when one starter leaves the tracked game

- Complete a running game on the first explicit idle/non-custom or changed-battle response from any recorded starter. Unknown status, substitutes, and failed API requests do not confirm completion.

## 2026-10-07 - faster scrim monitoring and manual game controls

- Poll live starters every five seconds by default instead of fifteen, retaining two full idle sweeps before automatic completion. Retry result history every fifteen seconds instead of sixty; provider pacing and rate-limit backoff remain enforced.
- Add manager-only Force start game and Force end game buttons, retaining stale-action protection, voice handling, recorded lineups, and exactly-once completion. Forced endings still verify statistics separately.

## 2026-10-07 - detect active Custom Room mode 301

- Recognize live player status mode 301 as Custom Room alongside mode 300. Start detection no longer falls through to the unsupported custom-game live-roster endpoint for this mode; idle rooms remain excluded.

## 2026-10-07 - exclude Visitors from scrim composition

- Ignore the stable Visitor role in the waiting queue, lineup, and substitute eligibility, including saved profiles and test sessions. Visitors in voice do not count toward team composition.
- Allow Visitors to stay in either scrim voice room, with speaking disabled in the main scrim room. Preserve this access during permission repair and restore tracked voice mute state in waiting.

## 2026-10-06 - responsive application submission and decisions

- Publish reviewable applications immediately after required account verification; enrich player stats in the background without holding the applicant lock. Discard results after decisions or form edits.
- Keep short account search/verification requests separate from long stats reads, with three concurrent identity requests and SDK pacing/cooldowns retained.
- Confirm decisions after roles/state are saved and before DM delivery and ticket deletion. Prevent failed feedback to deleted channels from causing cascading unhandled errors.

## 2026-10-06 - private bot health command

- Add `/ping` with Discord gateway latency and a restart-persistent 24-hour bot error count plus the latest five timestamps/sources. Replies are ephemeral and exclude raw log messages and tracebacks.

## 2026-10-06 - six-player scrim posting minimum

- Require at least six distinct eligible players available for the full scrim interval before posting or accepting votes.
- Withdraw existing posts below the six-player minimum on refresh, clearing their votes and updating the summary.

## 2026-10-06 - renamed channels and mobile scrim summary links

- Reuse saved scrim channel IDs and their category during setup after renames, avoiding duplicate channels. Deliver canonical server scrim results using the stable log-channel ID while retaining the environment override.
- Show every tied most-voted scrim on its own line with its vote count, local timestamp, and separate Jump to message link for mobile use.

## 2026-10-05 - bottom-of-channel scrim vote summary

- Maintain a silent Most voted scrim summary with the leading vote count and comma-separated links to every tied upcoming finder post, including zero-vote ties.
- Edit it after votes and refreshes, and move it below new channel messages. Persist its identity, recover interrupted sends, and exclude expired/withdrawn posts.

## 2026-10-05 - expire finder posts at scrim start

- Delete finder posts at their advertised start regardless of vote count, and reject late votes. Never publish already-started offers or revive expired slots.
- Retain the minute refresh interval, saved rank filters, and future-slot vote persistence.

## 2026-10-05 - supervise scrim collection with the main service

- Start the configured collector automatically with the shared per-user service, using its own virtual environment and private settings. Expose collector status/PID/last exit alongside bot status.
- Restart either child independently after a crash; shared restart and stop control both owned process trees. Collector startup failures do not stop the main bot, and restart rechecks configuration.
- Add a per-feed collector instance lock. Reconnect catches up missed messages and resumes persisted extraction, preventing a healthy publisher from silently relying on an offline collector's stale feed.

## 2026-10-05 - shared per-user Windows bot service

- Run one hidden supervisor outside chat terminals, with automatic bot crash recovery and startup at Windows sign-in without administrator rights.
- Provide shared status/start/restart/stop controls, restrict termination to the owned child tree, and retain OS duplicate-instance locks.
- Document the canonical checkout and agent controls; keep runtime state and logs private.

## 2026-10-05 - preserve votes across same-start scrim replacements

- Keep the saved voter mentions and count when newer offers, fallback sources, or rank-filter changes update an existing start-time message, including a change of opponent.
- Preserve votes across restarts, the six-person cap, and self-removal. Different starts have separate voters; expired or withdrawn messages still clear votes, and ineligible members are still removed.

## 2026-10-05 - Visitor role name and stable scrim permissions

- Rename the existing Member role to Visitor, preserving its ID, assignments, and permissions.
- Identify Visitor by its configured role ID during scrim setup instead of the legacy Member name/environment override.

## 2026-10-05 - immediate form update feedback

- Respond to the final click with Updating your form / Submitting your application immediately, change the button label, and disable controls while saving. Restore controls after a failure.

## 2026-10-05 - faster saved form edits

- Reuse unchanged verified account identities for accepted/pending edits instead of waiting for external account searches and verification; retain uniqueness checks and verification for changed accounts.
- Acknowledge Finish before waiting for locks, show Saving immediately, prevent duplicate submissions, and restore controls after failures.

## 2026-10-05 - welcome help field

- Move the video link into a Need help? field in the welcome embed, labeled Marvel Rivals.
- Keep only the applicant mention in welcome message content.

## 2026-10-05 - original scrim post timestamp

- Add Time posted beneath Original post, showing the original source message's creation date/time and relative age using Discord timestamps. Existing opportunity messages refresh silently; replacements use their own source time.

## 2026-10-05 - newest scrim per start with distinct opponent counts

- Group automatic offers by exact start, ignoring the end for grouping. Same-author/start bumps count once; show the newest qualifying source and use N Scrims Found! for multiple distinct opponents.
- Edit the same channel message silently when a newer source appears or the displayed source disappears or stops matching. Fall back to the next newest eligible source; remove the message once none remain.
- Preserve availability, minimum-hour, rank, expiry, and voting restrictions. Reset votes when changing opponents, retain votes for same-author bumps, and reject stale-source votes before refresh.
- Persist stable message identities and current source metadata across restarts, recover interrupted sends, and remove legacy duplicate posts before delivery. Apply newest-per-start selection to manager previews too.

## 2026-10-05 - short scrim notification title

- Use Scrim Found! as the automatic opportunity title, retaining the advertised date/time and start-only end notice in the details. Existing message updates remain silent.

## 2026-10-05 - manager-only scrim opportunity discussion

- Make Marvel Rivals team/tryout roles read-only in the scrim opportunities channel, including thread messages and creation. Allow Marvel Rivals managers to send messages and use threads.
- Preserve channel visibility, bot posting, and existing vote buttons. Owner-managed finder overwrites remain excluded from onboarding permission repair.

## 2026-10-05 - enforce full opponent rank range

- Require the entire advertised opponent rank range to fit within the saved search. Diamond–Celestial retains Diamond–Grandmaster but removes Celestial–Eternity and ranges starting below Diamond.
- Apply the same containment rule to channel refreshes, command previews, and voting; keep single-tier searches limited to that tier and its divisions.

## 2026-10-05 - fresh scrim channel rebuild and start-only display

- Add an explicitly confirmed channel rebuild tool that clears only the destination messages, message mappings, and votes, then publishes eligible collected offers as new posts. Preserve collected source data and the rank filter; use the bot instance lock to avoid races.
- Restore everyone visibility at the owner's request before rebuilding the opportunity posts.
- Start-only offers display only their real start and End time not advertised. The one-hour matching/expiry rule stays internal; no invented end is shown.

## 2026-10-05 - ping every new scrim post

- Ping matching players on every new channel post, including offers that return after changing the rank filter. Edits to existing posts and vote updates remain silent.

## 2026-10-05 - saved scrim rank searches and clearer timing

- Add owner/Marvel-manager `/scrim_rank min_rank max_rank` to save the default opponent search for both automatic channel posts and `/scrim_opportunities`. Maximum is optional; one rank searches that tier, aliases/divisions are supported, and Any clears the filter.
- Match opponent ranges that overlap the selected range; refresh channel posts immediately when the filter changes, and enforce it on voting.
- Label future offers Upcoming with a relative start time and started offers In progress with a relative end time. Retain actual advertised times and existing player pings.
- Preserve owner-managed scrim finder channel overwrites during onboarding repairs so a hidden testing channel is not automatically reopened. Restore the requested hidden testing visibility while retaining bot access.
- Use the Eastern date/time range as the opportunity title, remove the Available players field and attendance disclaimer, and retain player mentions in message content. Updates and previously posted offers returning after a filter change do not re-ping.

## 2026-10-05 - automatic scrim matching and votes

- Publish collector offers in the designated scrim channel when at least four current, accepted Marvel Rivals tryouts/team members/managers can cover the full advertised duration. Start-only offers require one continuous hour; advertised durations shorter than one hour do not qualify.
- Convert saved weekly schedules using each player's time zone and the scrim date, including overnight windows and daylight-saving rules. Rank is context, not an eligibility filter.
- Ping matched players in new message content, show opponent details and a Voted x/6 roster, and provide persistent green Vote/red Remove vote buttons restricted to current registered Marvel Rivals members. Votes are unique, capped at six, and saved across restarts.
- Refresh every minute, update changed posts without repeated pings, and delete posts once the offer ends, its source is removed, or fewer than four eligible players remain. Persist message mappings and recover interrupted sends to avoid duplicates.

## 2026-10-05 - repair scrim opportunity finder

- Pass the collected offer list to the manager preview so populated feeds render and empty feeds return the intended message.
- Add command-level regression coverage using temporary feed databases.

## 2026-10-05 - full YouTube URL

- Use the full YouTube watch URL beside the welcome mention.

## 2026-10-05 - move video to welcome message

- Place the YouTube URL beside the member mention above the welcome embed, including existing welcome messages during repair.
- Remove the video link from ephemeral day-selection messages.

## 2026-10-05 - profile owner's local clock

- Show Current time as plain clock text in the profile owner's saved time zone, including daylight-saving abbreviation.
- Keep availability as viewer-local Discord timestamps and clarify the footer applies to availability.

## 2026-10-04 - Rivals API 5.3 update

- Require rivals-api 5.3.0 with rank recovery, provider outage cache fallbacks, and additional public account-search fallback.
- Preserve browser support and the existing normal/precise lookup choices.

## 2026-10-04 - Rank recovery and private-profile accuracy

- Display recovered current and lifetime peak ranks from the SDK's rank summary.
- Keep private-profile stats visible with an accuracy warning and label cached
  outage data when the SDK reports it.
- Show missing rank data as unavailable and accept dictionary rank records.

## 2026-10-04 - readable stats footer

- Replace repeated provider/attribution diagnostics with short, deduplicated notes about incomplete stats, unavailable stats, or missing hero rankings.
- Keep partial overall rates clearly labeled with the included game modes and refresh application embed caches.

## 2026-10-04 - Rivals API rate-limit update

- Require rivals-api 5.2.0, retaining browser support and normal/precise lookup selection.
- Use the SDK's randomized request pacing, shared provider cooldowns, and preservation of usable summaries when other providers fail.

## 2026-10-04 - Show available partial overall rates

- Display SDK `partial_result` overall rates when full requested-mode counts
  are unavailable, with an explicit note naming the available modes. Keep missing
  data unavailable when no measured fallback exists. Preserve healthy provider
  hero/class results and their coverage notes.

## 2026-10-04 - Conservative Rivals request pacing

- Default to three-second request gaps and sixty-second fallback cooldowns per
  provider. Configure them with `RIVALS_API_REQUEST_INTERVAL` and
  `RIVALS_API_RATE_LIMIT_COOLDOWN`; SDK provider limits remain shared across
  client instances. Provider Retry-After takes precedence over fallback timing.

## 2026-10-04 - Browser recovery for provider blocks

- Enable browser fallback for normal profile lookups by default, with explicit
  `RIVALS_API_BROWSER_FALLBACK=false` opt-out. Install the browser dependency and
  document fetching the Camoufox binary.
- Require rivals-api 5.1 for shared provider pacing and HTTP 429 cooldowns.
  Browser fallback recovers supported HTTP access blocks; it does not bypass
  provider rate limits or guarantee availability. Scrim monitoring retains its
  explicit browser-disabled setting.

## 2026-10-04 - Rivals API 5 lookup methods

- Upgrade to rivals-api 5.0.0; omitted calculation method uses the SDK's normal summary-based default for current-season rates.
- Add optional Normal/Precise choices to `/lookup`, applying the selected method to overall, hero, and class rates consistently.
- Show partial-coverage notes in lookup results and refresh application stats caches without changing forms or membership.

## 2026-10-03 - username-only public search suggestions

- Display only the username for Rivals API autocomplete results; keep account UIDs internal for selecting the correct account.

## 2026-10-03 - combine member and public account search

- Always search Rivals accounts for autocomplete queries of at least two characters, including queries matching saved members; list saved members first.
- Run bounded account searches independently from slow stats reads, cache results briefly, and retain member suggestions when public search is unavailable.

## 2026-10-03 - simplify lookup suggestions

- Remove Saved member and In team labels from `/lookup` autocomplete; retain saved game and Discord usernames.

## 2026-10-03 - member profile selection

- Add an optional member argument to `/profile`; default to the caller and allow viewing another member's saved game form and availability.
- Keep responses ephemeral and require a completed current-version form for the selected member.

## 2026-10-03 - profile Discord timestamps

- Display `/profile` current time and availability windows as Discord time-only timestamps.
- Convert saved availability from the member's selected zone; Discord renders each viewer's local time.

## 2026-10-03 - own form profile

- Add `/profile` without arguments to privately show the caller's saved game form, current local time, and weekly availability in their selected time zone.
- Render configured question fields for the saved game; keep internal account and workflow metadata out of the profile.
- Use general game wording in profile and lookup command descriptions, with no form changes or membership resets.

## 2026-10-03 - searchable account lookup

- Replace `/profile` with `/lookup` and a required text query accepting names or pasted mentions.
- Match saved game names and Discord usernames/display names; prioritize saved-member autocomplete with team labels.
- Use the form's public account search for other names, select by UID, and show external account stats without saved form fields.
- Keep autocomplete provider work bounded and require explicit selection for ambiguous member matches.

## 2026-10-03 - availability video

- Show the owner's YouTube video in message content when applicants reach day selection, including form edits.

## 2026-10-03 - delete member data on departure

- Delete departing members' saved forms, stats caches, profile/availability, account links, and restoration metadata.
- Remove private tickets and catch departures missed while offline during reconciliation.
- Rejoining starts fresh onboarding without saved answers or role restoration.

## 2026-10-03 - release rejected account claims

- Stop rejected/visitor forms and unfinished drafts from reserving game accounts; retain their prefilled answers.
- Include the actively linked member's Discord mention in duplicate-account errors.
- Preserve account reservations for accepted/pending members and saved reset restoration.

## 2026-10-03 - self-service form editing

- Add `/edit_form` for everyone to edit their own current saved game form with prefilled answers.
- Save validated completed-profile edits without tickets, manager review, or role changes; pending applications update in place.
- Preserve reset completion requirements and invalidate edits when application status changes.

## 2026-10-03 - current-season Rivals stats

- Upgrade rivals-api to 4.1 and explicitly request season="current" for overall, hero, and class win rates.
- Label all three fields Current Season and remove the overall all-season fallback.
- Refresh cached application stats without resetting forms or roles; preserve hero leaderboard placements.

## 2026-10-03 — repair duplicate hero fields and rank lookup

- Remove the duplicate Competitive hero field from /profile; display one combined Top 6 Characters field.
- Fetch hero leaderboard placements before detail-heavy stats and try both summary modes independently.
- Keep rank failures retryable rather than caching rankless embeds as complete results.

## 2026-10-03 — migrate to rivals-api 4 default win rates

- Upgrade to canonical stats calls with automatic caching and default Competitive plus Quick Play scope.
- Keep current-season overall rates and all-available-season hero/class rates; preserve leaderboard placements through the renamed summary endpoint.
- Show partial/unresolved coverage and refresh older embeds; if current-season metadata is blocked, show a clearly labeled all-seasons overall rate.
- Add hero and class fields to /profile and keep scrim monitoring behavior unchanged.
- Keep displayed labels concise without mode names; refresh old labels in pending applications.

## 2026-10-03 — restore starter-only test behavior

- Remove the test-host requirement from rerolls at the owner's request; substitutes do not trigger game detection.
- Keep restored waiting-room configuration and current-profile account lookup fixes.

## 2026-10-03 — preserve live detection through test rerolls

- Require the real test host in rerolled example lineups so the monitor never loses its live account.
- Repair the active test lineup without replacing the other five starters; verify its existing Discord panel changes to In game from live Custom-game evidence.
- Cover required-host rerolls against simulated players with stronger main-role combinations.

## 2026-10-03 — restore waiting-room detection after rebuild

- Restore the wiped scrim channel mapping and reuse the existing dashboard message; verify the owner's current waiting-room attendance appears live.
- Supply verified game UIDs from the new member/profile store instead of the deleted legacy account-claims table.
- Add safe channel-recovery tooling and regression coverage with the actual rebuilt database schema.

## 2026-10-03 — show stats for Quick Play-only applicants

- Reproduced empty Competitive fields on the pending account: its available history contains Quick Play and Custom games.
- Fall back to explicitly labeled cached Quick Play hero/class rates when Competitive hero history is empty; preserve hero placements.
- Refresh old stats embeds and avoid permanently caching empty/partial results.

## 2026-10-03 — applicant editing

- Add a persistent applicant-only Edit button beside Accept/Reject.
- Prefill the current form and schedule; validated resubmission updates the same pending application message.
- Keep reviewed answers unchanged while an edit is unfinished and reject edits after a manager decision.

## 2026-10-03 — repair season win-rate fallback

- Keep exact-first history lookup followed by cached season/hero/class calculations.
- Use the documented profile season win rate when cached history has no usable season result; preserve valid zero rates.
- Refresh older cached application stats embeds in place and retain hero leaderboard placements after win rates.

## 2026-10-03 — restore player data in applications

- Remove application.txt attachments, including from existing application messages when repaired.
- Visitors can view every Marvel Rivals channel and its history, but cannot send messages, create/post in threads, or join voice.

- Added a second Marvel Rivals player-data embed to the same message as the tryout application.
- Includes current/peak rank, current-season win rate, class win rates, and top competitive characters from the existing public player lookup.
- Cache successful stats for restart/repair; provider failures show an unavailable-data embed without losing the application.
- Render weekly availability as Discord timestamps in both the editor and final application, using the applicant's saved time zone.

## 2026-10-03 — availability timing and duplicate-prompt repair

- Do not treat unfinished version-zero drafts as outdated completed applications during periodic reconciliation.
- Reuse the saved welcome message through a real form reset instead of creating a second prompt.
- Updated availability instructions to the owner's requested wording.
- Start-time selection now immediately filters end times to at least one hour later, through midnight; invalid earlier selections are cleared.

## 2026-10-03 — restore onboarding presentation and availability controls

- Restored the MR emoji and embedded welcome/tryout panels, and username-based ticket names with a short ID suffix.
- Replaced the availability text box with a repeatable day → start/end time → more days → finish flow, with cached schedules and overnight windows.
- Added the normal-character username search note and a paginated game-account results picker; selected accounts are verified by UID.
- Updated existing tickets/panels through reconciliation, without wiping data or triggering a form-version reset.
- Added applicant mentions in message content, with explicit mention permissions for new welcome/application messages.

## 2026-10-03 — organization workflow rebuild

- Deleted previous onboarding/scrim databases, backups, caches, logs, and obsolete docs without retaining member data.
- Split onboarding into cogs, services, storage, versioned forms, and persistent UI.
- Unified new-member/visitor applications; removed user close controls; restricted review to selected managers; corrected rejection DMs.
- Added cached-answer resets, exact role restoration including managers, owner onboarding, completion-based repair, and an instance lock.
- Organized retained scrim/provider modules and regression coverage.
- Added agent commit rules and private runtime exclusions for public source publication.
- Added the owner's explicit review override, including self-review, without granting other managers that exception.
- Automatically accept the owner's completed initial form; reset returners restore automatically without a review panel.
