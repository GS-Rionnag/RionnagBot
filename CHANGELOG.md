# Change timeline

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
