# Change timeline

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
