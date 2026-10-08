# Operations

The canonical Windows runtime is the per-user background supervisor in this workspace. It runs independently of chat terminals, starts both the bot and the configured scrim collector, restarts either child independently after 15 seconds, and starts hidden when the owner signs into Windows. No administrator rights are needed. Before-sign-in startup is not supported by this per-user setup; the PC must be awake and connected for Discord access.

All agents working under this Windows account must manage this same checkout and supervisor, rather than launching their own bot or a worktree copy:

```powershell
rtk proxy .venv\Scripts\python.exe -m rionnag.service status
rtk proxy .venv\Scripts\python.exe -m rionnag.service restart
rtk proxy .venv\Scripts\python.exe -m rionnag.service stop
rtk proxy .venv\Scripts\python.exe -m rionnag.service start
```

`install` creates/updates `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\RionnagBot.vbs` pointing to this checkout and starts the supervisor. Re-run it if the checkout moves. To disable automatic startup, remove that specific Startup file and run `stop`. Stopping alone lasts until the next sign-in or explicit start. Runtime control/status files are private in `data/service/`; supervisor output is in `logs/service.log` and bot logs remain in `logs/bot.log`. Logs may contain private information; inspect privately. Status indicates process health, not Discord readiness; confirm the current gateway connection in bot logs. Restart terminates only the supervisor's own live child process tree, and waits for it before replacement. Concurrent starts are guarded by an OS supervisor lock, and the bot retains its separate instance lock. Never kill all Python processes. Manual `-m rionnag`/bot.py launches are for supervised troubleshooting only, with the service stopped first. Startup failures retry every 15 seconds; fix the underlying configuration rather than launching competing copies.

Generated state is in data/ and logs/ and never belongs in public Git.

The collector starts whenever `scrim_collector/.env` contains nonempty DISCORD_USER_TOKEN and SCRIM_SOURCE_CHANNEL_IDS. Install its requirements in `scrim_collector/.venv`; the supervisor uses that environment rather than the bot's discord.py environment. Its shared feed defaults to data/scrim_feed.sqlite3. `status` includes collector_state, collector_pid, and collector_last_exit; a running bot alone does not prove collection is running. Missing collector dependencies/runtime retry without stopping the main bot. Shared `restart` rechecks collector configuration and replaces both owned children; `stop` stops both. Collector gateway and extraction diagnostics appear in logs/service.log, and recent history is caught up on reconnect. A feed lock prevents duplicate collectors. When changing supervisor code itself, run `stop` then `start` once so the supervisor loads the new code; future ordinary restarts use the shared `restart` command.

Rivals stats require rivals-api 5.1 or later. Normal summary-based calculation is the SDK default. `/lookup` accepts an optional method choice: Normal or Precise. Omitted method is not passed to the SDK; Precise traverses available match history and details and can take several minutes. Browser fallback is enabled by default for profile lookups; install `rivals-api[browser]` and run `python -m camoufox fetch`. Set `RIVALS_API_BROWSER_FALLBACK=false` to opt out. A live A G N 1 normal lookup recovered all three stat categories through Tracker's browser fallback on October 4; provider coverage remains uncertain and RivalsData still returned a genuine rate limit. HTTP 429 cooldowns are respected rather than bypassed with a browser. The bot requests three-second minimum gaps per provider and a sixty-second fallback cooldown; adjust `RIVALS_API_REQUEST_INTERVAL` and `RIVALS_API_RATE_LIMIT_COOLDOWN` if needed. Server Retry-After headers override the fallback cooldown. Availability is not guaranteed. All three win-rate calls explicitly use season="current"; unavailable current-season data is reported without substituting all-season totals. Precise hero attribution can require one detail read per tracked match. Application stats use normal calculations and retain their five-minute timeout while interactions remain deferred. Scrim monitoring retains its existing browser-disabled request pacing.

## Form changes

1. Edit config/forms.json, preserving stable existing question keys and adding unique keys for new questions.
2. Increase the game's version integer. Changes without version increases and version decreases are rejected before mutation.
3. Test and commit. Use owner-only `/reload_forms` or restart. Outdated members, including the owner, lose assignable roles except Rionnag and receive private tickets with cached answers.
4. Accepted returners complete current required questions and get automatic role restoration. Pending/rejected/new applicants need manager review.

Do not delete the database for routine form changes: it contains restoration snapshots. `/onboard member` repairs state without intentionally invalidating completed forms.

## Commands and recovery

### Hosting scrims

Channel 1557583583143526460 contains the persistent **HOST SCRIMS** board. **Choose a day** opens a private
day selector with no time windows yet. Days use the player's saved time zone and show distinct confirmed
and available players across qualifying sessions (overlapping slots never double-count a player). Selecting
a day opens exact start/end windows with counts for each time; only intervals the player can fully attend
appear. Both stages rank confirmed attendance first, available attendance second, then best-role fit.
**My selections** uses the same day-first flow to withdraw commitments. Times in embeds render in each viewer's
local zone; dropdown labels use the saved time zone. Saved schedules determine
availability for the full session. Suggestions span fourteen days at thirty-minute starts, defaulting to
two-hour sessions. Missing schedules or impossible 2 Tank / 2 DPS / 2 Support compositions are excluded.
Only current accepted Marvel members with restored profiles and live game roles count; Visitors never count.
Attendance outranks best-role fit; third/worst roles are never used. Confirmations are
uncapped so extra players can be substitutes. A slot needs a valid composition among confirmed players
before it is Ready; six votes alone are insufficient.

The public board shows no suggested sessions before the first confirmation. Once a player confirms an
exact interval, that session appears publicly with confirmed/available counts and member mentions followed
by their saved best roles. These are preferred roles, not promises of final lineup assignments. Existing
votes survive deployment, so previously confirmed sessions remain visible. Mentions do not send notifications.
Public confirmed sessions sort by confirmed-player count descending, then scrim start ascending (soonest first).

The public board has only **Choose a day**, **My selections**, and **View lineups**. Host a session,
Host settings, and `/scrim_host_settings` have been removed. Hosting retains the saved session duration
and advert destination, defaulting to two hours and the sole configured collector source channel.
There are no opponent rank limits. `/scrim_host` points members to the board.

A separate ten-second loop checks for six confirmed players forming a valid 2–2–2 lineup at an exact
interval. It DMs the server owner with that date/time and **Yes, send advert** / **No** buttons. Only the
owner can answer. Yes revalidates the current lineup, fetches anonymous current/peak ranks, and queues
the advert; No publishes nothing. The DM is independent of the public board. The owner's DMs must permit
bot messages; failed deliveries remain pending for retry. Declining suppresses repeated prompts while
the interval stays ready. If readiness is lost and later regained, a fresh request invalidates old buttons.
Approval messages/decisions persist and their buttons restore after restart. Interrupted sends recover
through the owner's bot DM history using a stable marker. An expired or broken lineup cannot publish.

Rank-only profile reads use linked UIDs and existing provider pacing; results cache for ten minutes.
Missing/unavailable ranks say Unavailable rather than guessing. The external text contains
`LFS at <t:UNIX:F>` and Player1–Player6 current/peak ranks. It contains no usernames, account UIDs, mentions,
or profile links. You handle opponent conversations yourself.

The shared collector handles an outbound queue in the private main database every ten seconds, separately
from offer extraction. It must be logged into this server owner's account. `SCRIM_HOST_CHANNEL_IDS` in
the collector's private environment can specify an outbound allowlist; otherwise existing
`SCRIM_SOURCE_CHANNEL_IDS` are used. The collector rechecks current roles, completion, commitments,
availability, settings, and 2–2–2 before sending. Queued requests survive restart. Each session has one
publication identity; interrupted/uncertain sends search owner-account history for the exact content and
never blindly resend. Uncertain delivery requires checking the external channel manually. Failed queued
requests require operational review; repeatedly clicking an answered approval never duplicates an advert.
No external messages are deleted.

The board displays delivery status and advert links and flags a selected roster when a starter withdraws,
leaves, resets, or changes their roles/availability. Published anonymous rank text remains the original
snapshot; discuss replacements with the opponent yourself. Already published intervals do not trigger
another approval or repost merely because more players confirm.
Existing finder posts, votes, and voice/game tracking are independent. No form definition/version changes
or membership resets are needed. `python -m tools.inspect_hosting` checks board delivery without printing
player answers or credentials.

Applications become reviewable after required identity verification without waiting for optional Rivals stats. Stats load in the background (up to five minutes) and cannot block acceptance or overwrite a changed/decided application. Account search/verification uses a separate three-request limit from full stats reads while preserving SDK pacing/cooldowns. Decisions confirm saved roles/state before DM delivery and ticket cleanup; closed DMs still retain retry notifications.

`/ping` privately reports Discord gateway latency and ERROR/CRITICAL entries from the bot log over the last 24 hours, including the latest five timestamps and logger sources in server log time. Counts can include duplicate reports of one failure. Raw messages, tracebacks, collector errors, and warnings are excluded. Missing/unreadable logs are reported as unavailable, rather than healthy.

Member departure permanently deletes their saved onboarding/profile data and private ticket. Startup repair catches departures missed offline. Rejoining requires a fresh form; previous roles and answers are not restored.

`/edit_form` is available to everyone and opens their own saved game form with prefilled answers. Finish every page and availability to save. Completed membership roles stay unchanged; pending applications update in place. Members without saved game data finish onboarding first.

Accepted/pending members keeping their linked account skip external account search and re-verification. Changed accounts still use provider verification; pending review updates may fetch missing player stats. Finish shows Saving immediately and prevents duplicate saves. Failed saves restore the controls for retry.

`/profile` privately shows your saved current-version game form by default; the optional member argument shows another member's profile. It includes their in-game username, configured form fields, current time as plain clock text in the profile owner's saved time zone (at command invocation), and free days/time windows as Discord time-only timestamps. Availability is calculated from your selected time zone and upcoming weekdays, including overnight endings; Discord renders timestamps in each viewer's local time zone. It follows the saved game's definition without a game selector; unfinished/reset members finish their form first. `/lookup` remains the separate account stats search.

`/lookup query` shows account stats and requires a Discord username/display name, pasted mention, or Marvel Rivals username. Autocomplete prioritizes saved members, displaying their saved game username and Discord username without membership labels. Queries of at least two characters also use the form's public account search alongside saved-member matches. Account searches run independently of slow stats reads, with at most three concurrent searches and a short cache. Slow/unavailable search retains saved-member suggestions; typed names can still be submitted. Ambiguous member names require selecting a suggestion. Saved members retain their profile fields; external accounts show public stats only. `/promote` lets game managers promote tryout → team → manager. `/reload_forms` and `/onboard` are owner-only. Existing `/scrim` controls configure lobbies and voice rooms. `/scrim_opportunities` previews collector offers for Marvel managers.

The scrim channel configuration was restored after the deliberate wipe, reusing the existing scrim-control message, Scrim Waiting Room, and Scrim voice channel. Previous matches, queues, and feeds remain deleted. `/scrim setup` can change the mapping; `scripts/repair_scrim_channels.py` recovers these known channels only when configuration is missing and refuses to replace existing settings. Restart refreshes the saved dashboard with current voice attendance. The optional collector needs its own environment/private .env. Its default feed is ../data/scrim_feed.sqlite3 relative to its folder.

Automatic matched offers go to channel 1555989494451146802. Keep the collector on the default shared feed path; the bot checks every sixty seconds. It needs View Channel, Read Message History, Send Messages, and Embed Links there. Only accepted Marvel Rivals players on the current form with a live tryout/team/manager role count or vote. At least six must be available for the full advertised interval; start-only posts assume one hour. Dates/times display in each viewer's Discord time zone. New posts ping every matching player in content; updates and votes do not re-ping. Green Vote/red Remove vote update the saved Voted x/6 roster, with six unique voters maximum. Posts are deleted at their advertised start regardless of vote count, when their source disappears, or when they no longer match six players. Refresh timing can delay an update/deletion by up to one minute. Completing or editing a form updates eligibility on the next refresh; no form version change or membership reset is needed. Voting does not confirm a booking.

Owner/Marvel managers can set the shared default opponent-rank search with `/scrim_rank min_rank:GM` (Grandmaster only) or `/scrim_rank min_rank:Diamond max_rank:Grandmaster`. Opponent ranges qualify only when their entire range is within the selected range; an omitted maximum uses the minimum tier, including all its divisions. Division-specific searches such as GM I are supported. `/scrim_rank min_rank:Any` clears the filter. Settings survive restart and affect both the automatic channel and `/scrim_opportunities`; updates immediately refresh the channel. Future offers are labeled Upcoming and started offers In progress, with Discord relative times.

Opportunity titles say Scrim Found! for one matching opponent or N Scrims Found! for multiple opponents at the same start. The date/time details beneath them render in the viewer's local time. Player mentions remain in message content, without a duplicate Available players field. Every new channel message pings matching players, including slots returning after a filter change. Existing posts and vote counts are edited without notifications. Start-time expiry removes all finder posts within the next refresh, regardless of vote count. Already-started offers are never published.

Time posted appears beneath Original post and shows the displayed source message's creation date/time and relative age in the viewer's local time. It comes from the Discord message ID in the original link, so replacement and fallback show the correct source time even when the finder message itself is reused.

A Most voted scrim summary stays at the bottom of the finder channel. Each tied upcoming post has its own line with its vote count, local timestamp, and separate Jump to message link for mobile use; zero-vote ties include all upcoming posts. Votes and periodic refreshes update it silently. New channel messages cause the bot to delete/repost only the summary at the bottom without pings. Its identity survives restart and interrupted sends are recovered from its footer. When no upcoming posts remain, it says so. Discord delivery or permission failures retry on the minute refresh.

One channel message represents each exact start time, regardless of the advertised end. Same-author reposts for that start count once. The newest qualifying source is shown; a newer post edits that message silently, and deletion or loss of eligibility falls back to the next newest matching source. Counts update with the feed and rank filter. Availability still has to cover each candidate's own advertised interval, or one hour if no end is advertised. Existing voter mentions and counts stay with the same start-time message through replacements, fallback, and rank-filter updates, including a change of opponent. Different starts have separate votes; removing or expiring the message clears its votes, and ineligible members are still removed. `/scrim_opportunities` also collapses reposts and shows the latest rank-matching source per start. Existing duplicate channel messages are removed during refresh; permissions and source data are preserved.

When an opponent gives no end, show only the start and End time not advertised; the one-hour availability/expiry assumption is internal. An explicitly owner-authorized fresh rebuild can use `scripts/rebuild_scrim_opportunities.py --confirm-channel 1555989494451146802` with the bot stopped. This deletes every destination-channel message and its saved mappings/votes, enables everyone visibility, and republishes eligible collected offers with fresh pings. It preserves the source feed and saved rank filter. Never run it without explicit authorization for that rebuild.

The owner manages the finder channel's visibility manually. Onboarding repair skips this channel to preserve its hidden testing permissions across restarts and five-minute reconciliation. Reveal it manually when ready; routine repairs will preserve that choice too. Player pings remain enabled in new messages.

The opportunities channel is currently visible to everyone, with Marvel Rivals team/tryout roles read-only and Marvel Rivals managers allowed to post and use threads. Vote buttons still work for registered eligible players; they do not require Send Messages. Channel-specific role overwrites persist through repairs and restarts.

Role failures: place the bot above all membership roles, below Rionnag. Check configured IDs/logs. Failed reconciliation retries every five minutes. Completed members never receive new tickets solely due to restart. Closed DMs retain a retry notification and post the result in the ticket. Resume forms through ticket buttons.

Inspect staged paths before public pushes. Exclude .env, data/, logs/, backups, transcripts, and provider caches. Future agents must commit/push coherent minor/major changes and maintain the timeline.

Lookup retains available private-profile stats and warns that they may be
inaccurate. SDK rank summaries can recover current and lifetime peak ranks;
missing ranks display as unavailable. Last-known provider responses carry a
cached-data warning when reported by the SDK. These additions require the
corresponding SDK update and a bot restart to take effect in the running service.

The organization uses discord.py's [cogs](https://discordpy.readthedocs.io/en/latest/ext/commands/cogs.html) and [persistent view registration](https://github.com/Rapptz/discord.py/blob/master/examples/views/persistent.py).

Scrim live polling defaults to five seconds between probes (SCRIM_POLL_INTERVAL, minimum five), ending a game on the selected starter's first explicit departure from the tracked game. Live/history checks stay with one real starter, preferring a currently selected player whose history previously verified a game. Provider pacing, response time, shared monitoring work, and error backoff can add delay. Managers can use Force start game or Force end game on the current dashboard when detection lags; these record normal transitions, preserve completion counts, and verify results separately.

Use manager-only /scrim end to fully close the active scrim session and restore voice state. Completed games remain saved; an active unfinished game is aborted without completion credit. Force end game only completes the current round and keeps the session open.

Current scrim voice operation: one shared Scrim room (the former waiting room). Tracking never moves, mutes, unmutes, or changes per-member voice permissions. Visitors and substitutes may stay and speak but Visitors are excluded from composition. Automatic Custom Room detection, game data collection, force game controls, and /scrim end remain available. Ending a game/session leaves everyone in place. Legacy waiting_id/stage_id storage keys both point to the retained room.

The owner-authorized October 7 consolidation uses scripts/consolidate_scrim_voice.py --confirm-remove 1555398841333850113 with the shared service stopped. It preserves a private database backup, restores tracked mute state, transfers occupants once, reuses the old main room name, updates saved mappings, and deletes only the confirmed old main voice room. Do not run channel deletion migrations without explicit owner authorization.

Edit lineup Force in/out treats an explicit pair as mandatory. Choose the incoming player first for Force in, or the outgoing player first for Force out; choose their counterpart second. Retained starters may change roles, preferring saved secondary roles and minimizing off-role overrides. Confirm the displayed roster before applying. Off-role overrides do not alter saved role preferences or completion counts.

History verification retries until verified without a time cutoff, including ended sessions. The saved starter must belong to that game's recorded lineup. Fetch only their newest custom match from the primary history provider, without the win-rate exact method or older-history pagination; full match details must verify game identity, timing, and recorded teammates. If a newer game replaces the target before indexing, the target stays unverified rather than importing another game. Non-rate-limit failures retry after sixty seconds; provider cooldowns still apply. Late verified game logs publish before the previous unverified session summary is removed and replaced by updated totals at the bottom.
