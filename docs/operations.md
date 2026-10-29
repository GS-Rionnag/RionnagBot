# Operations

The Third Eye's trollbot project now runs separately at the owner's request. It is
uninstalled from this environment and must not be loaded by this bot. Control it from
`../trollbot` with its own `.venv\Scripts\python.exe -m trollbot start|stop|status`.
It reads this checkout's existing `.env` privately to reuse the bot identity, and syncs
only The Third Eye's commands. This supervisor continues to manage only Rionnag and
the collector. Trollbot has separate logs, saved uwu locks and a single-instance lock.

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

## Source change notifications

GitHub repository `GS-Rionnag/RionnagBot` sends push events to the native Discord GitHub webhook in RIONNAG eSports `#changes` (channel `1557939894767325294`). GitHub uses JSON payloads. The webhook displays commit messages and links to commits. Its private Discord URL is configured only in GitHub repository webhook settings, with `/github` appended; never put that URL in source, logs, or support output. To check delivery, inspect the repository's webhook deliveries and the corresponding message in `#changes`. This notification is independent of the bot runtime.

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
day selector with no time windows yet, limited to the five soonest qualifying days. Days use the player's
saved time zone, ordered soonest first, then confirmed and available attendance. Each day lists distinct
unconfirmed available-player mentions with a count; overlapping slots never double-count a player. Selecting
a day opens exact start/end windows with counts for each time; only intervals the player can fully attend
appear. Exact time choices rank confirmed attendance first, available attendance second, then best-role fit.
**My selections** uses the same day-first flow to withdraw commitments.
In Choose a day's time dropdown, confirmed times are checked; uncheck to withdraw, including
clearing all choices on that page. Other pages remain saved. My selections closes after a withdrawal.
Times in embeds render in each viewer's
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
The Scrim Controls message always shows a Scrim status embed above Waiting for players, including
No scrim upcoming yet when empty. Six-player confirmations come from saved hosting votes, so a
session remains visible even if eligibility or lineup needs review. It shows the exact time and
owner-message, advert-delivery, or officialized state. The HOST SCRIMS board shows the same status.
HOST SCRIMS remains the first introduction/control message. Three separate session messages show
the top confirmed sessions, with disabled placeholders for unused positions. Rank by confirmed-player
count descending, then soonest start, regardless of available-player count or whether counts tie.
Each session title is its Discord date/time timestamp; message labels indicate its place without embed footers.
Each session message has persistent Join scrim and Withdraw buttons. An eligible player whose saved
schedule does not cover the interval receives a private warning with Yes, join this scrim and Cancel.
Only Yes stores an explicit schedule override for that exact start and session duration. The commitment
survives refresh/restart, counts toward readiness, and is recognized by outbound validation. Withdrawal
clears the override; changed durations and lost membership eligibility invalidate it. Saved form schedules
remain unchanged. Best/secondary-only 2–2–2 constraints still apply.
Private selectors retain all choices.
The public Available players (number) section lists only unconfirmed available members.
Confirmed-player mentions group by saved best role as `Tank: @mentions`, `DPS: @mentions`, then `Support: @mentions`.

The public board has only **Choose a day** and **My selections**. View lineups, Host a session,
Host settings, and `/scrim_host_settings` have been removed. Hosting retains the saved session duration
and advert destination, defaulting to two hours and the sole configured collector source channel.
`/scrim_host_for member:` privately shows the same day and time choices for another eligible
member based on their saved availability and time zone. Its controls are read-only. The time
picker shows confirmed and remaining available member mentions for each exact session.
There are no opponent rank limits. `/scrim_host` points members to the board.

A separate ten-second loop checks for six confirmed players forming a valid 2–2–2 lineup at an exact
interval. It DMs the server owner with that date/time and **Enter ranks & send** / **No** buttons. Only the
owner can answer. Enter ranks & send opens a minimum/maximum rank form, prefilled Grandmaster / Celestial.
Submitting revalidates the current lineup and queues the rank-range advert; No publishes nothing.
When six or more players have joined any session, each joiner receives one DM saying the scrim is not yet
confirmed and that another message will arrive if it becomes official. Later joiners receive the same update.
The DM is independent of the public board. The owner's DMs must permit
bot messages; failed deliveries remain pending for retry. Declining suppresses repeated prompts while
the interval stays ready. If readiness is lost and later regained, a fresh request invalidates old buttons.

At three confirmed players, another ten-second loop invites the remaining available players by DM.
The invitation shows the exact date/start and end as Discord timestamps with Yes/No buttons. Yes confirms
that player after checking current eligibility, interval, duration, and schedule; No only declines the
invitation. The bot deletes the invitation DM after either answer. Each player receives at most one
invitation per exact start/duration. Decisions and buttons
survive restarts, interrupted deliveries recover from DM history, and closed DMs are marked undeliverable.
The owner can use **Remind unanswered players** on the approval DM to repeat invitations to players who
are currently available and have not answered; Yes/No respondents are skipped. A No response remains off
the available list after a later board join. The owner receives one approval DM per hosting start even if
the lineup temporarily breaks and returns. Approval controls refresh as confirmations return. Approval
messages/decisions persist and their buttons restore after restart. Interrupted sends recover
through the owner's bot DM history using a stable marker. An expired or broken lineup cannot publish.

The external text is `LFS Grandmaster - Celestial at <t:UNIX:F>` using the owner's entered rank range.
It contains no individual player lists, ranks, usernames, account UIDs, mentions, or profile links.
No automatic profile-rank lookup runs when approving the advert. You handle opponent conversations yourself.

The six-player owner DM lists confirmed players under Tank, DPS, and Support, using their saved best roles
like the main board. Persistent Bump post and Confirm scrim controls remain usable after an advert is sent.
Confirm scrim asks the owner for a final Yes/No choice. Yes sets the one current official upcoming
scrim on the hosting board. The bot first DMs confirmed players that the time is official, then
DMs available unconfirmed players with persistent Yes/No choices. Yes joins the scrim; No declines.
Confirmed players receive a reminder on the scrim's calendar day in America/New_York and again
30 minutes before its start. These DMs have durable identities and recover after a restart.
Official player notices have a Can't make it control for confirmed attendees. It removes that player from
the confirmed list and sends the owner a private update with the remaining players grouped by saved role.
The server owner alone sees Cancel scrim on their official notice; cancellation requires a second confirmation.
For each player and scrim, status and reminder DMs update the same bot message as the booking changes.
Bot-authored scrim DMs are automatically deleted after the booked session ends; other DM content is untouched.
The 30-minute reminder is a fresh message that replaces the previous official notice and is deleted when the
scrim starts.
Existing panels refresh with these controls. Only the owner can operate them; readiness and notice generation
are checked again. Bump post requires a delivered advert and no other pending bump. The posting account checks
the current team and destination, deletes only the recorded own advert, and reposts its exact text. Durable
phases recover interrupted deletion and uncertain delivery without blindly sending duplicates. The main
board shows bump status and updates the advert link. Confirm scrim marks the interval officially booked,
closes advertising/bumping and further player invitations for it, and updates the main board and owner DM.
The owner can use Cancel scrim on that approval DM before the start time; this removes the official booking,
refreshes the hosting and finder boards, and updates existing player notices with the cancellation.
This does not start a tracked game, move members, or change their saved profiles.

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
An official booking reserves its America/New_York calendar day. Other hosting sessions on that day
disappear from board choices and cannot receive new confirmations or adverts. The official session
remains visible and joinable through its own card or DM invitation. Finder posts on that day are
withdrawn with their votes, and stale finder buttons reject votes. Existing external adverts are
not automatically deleted. Voice/game tracking remains independent. No form definition/version changes
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

Automatic collected scrim offers go to channel 1555989494451146802. Keep the collector on its default shared feed path; the bot refreshes the board every sixty seconds. The read-only board lists future offers across dates with start/end time, opponent rank, and a link to the source advert. It does not list or mention players, provide signup controls, or send automatic DMs. The summary and detailed offer embeds stay within Discord's embed limit; longer lists use a compact overflow list. `/scrim_opportunities` is a private manager preview.

The owner or Marvel Rivals manager can adjust the shared rank search with `/scrim_rank`; it also affects `/scrim_opportunities`. The newest qualifying source for each exact start is shown, with same-author reposts counted once. The board recovers from its footer after interrupted delivery. A failed Discord edit retries on the next refresh. Existing official bookings remain durable.

The finder channel's visibility is owner-managed and excluded from onboarding permission repair. The Bots category and its testing text/voice channels are a separate surface: everyone can see them, while members cannot post, create threads, or join the testing voice room. Owner/Administrator holders necessarily bypass channel overwrites. The bot has explicit access to post in the testing text channel and join the testing voice room.

When an opponent gives no end, the board shows End time not advertised; one hour is assumed for eligibility. Do not run the old channel rebuild script without explicit owner authorization; it deletes destination messages and saved opportunity votes.
Role failures: place the bot above all membership roles, below Rionnag. Check configured IDs/logs. Failed reconciliation retries every five minutes. Completed members never receive new tickets solely due to restart. Closed DMs retain a retry notification and post the result in the ticket. Resume forms through ticket buttons.

Inspect staged paths before public pushes. Exclude .env, data/, logs/, backups, transcripts, and provider caches. Future agents must commit/push coherent minor/major changes and maintain the timeline.

Lookup retains available private-profile stats and warns that they may be
inaccurate. SDK rank summaries can recover current and lifetime peak ranks;
missing ranks display as unavailable. Last-known provider responses carry a
cached-data warning when reported by the SDK. These additions require the
corresponding SDK update and a bot restart to take effect in the running service.

The organization uses discord.py's [cogs](https://discordpy.readthedocs.io/en/latest/ext/commands/cogs.html) and [persistent view registration](https://github.com/Rapptz/discord.py/blob/master/examples/views/persistent.py).

Scrim live polling defaults to five seconds between probes (SCRIM_POLL_INTERVAL, minimum five), ending a game on the scrim creator's first explicit departure from the tracked game. The creator must be in and remains in the starting lineup through edits, rerolls, and substitutions. Live detection checks only that creator. History checks start with the same player and rotate to another real starter after five minutes without a verified result. Provider pacing, response time, shared monitoring work, and error backoff can add delay. Managers can use Force start game or Force end game on the current dashboard when detection lags; these record normal transitions, preserve completion counts, and verify results separately.

Use manager-only /scrim end to fully close the active scrim session and restore voice state. Completed games remain saved; an active unfinished game is aborted without completion credit. Force end game only completes the current round and keeps the session open.

Current scrim voice operation: one shared Scrim room (the former waiting room). Tracking never moves, mutes, unmutes, or changes per-member voice permissions. Visitors and substitutes may stay and speak but Visitors are excluded from composition. Automatic Custom Room detection, game data collection, force game controls, and /scrim end remain available. Ending a game/session leaves everyone in place. Legacy waiting_id/stage_id storage keys both point to the retained room.

The owner-authorized October 7 consolidation uses scripts/consolidate_scrim_voice.py --confirm-remove 1555398841333850113 with the shared service stopped. It preserves a private database backup, restores tracked mute state, transfers occupants once, reuses the old main room name, updates saved mappings, and deletes only the confirmed old main voice room. Do not run channel deletion migrations without explicit owner authorization.

Edit lineup Force in/out treats an explicit pair as mandatory. Choose the incoming player first for Force in, or the outgoing player first for Force out; choose their counterpart second. Retained starters may change roles, preferring saved secondary roles and minimizing off-role overrides. Confirm the displayed roster before applying. Off-role overrides do not alter saved role preferences or completion counts.

History verification retries until verified without a time cutoff, including ended sessions. The saved starter must belong to that game's recorded lineup. Fetch only their newest custom match from the primary history provider, without the win-rate exact method or older-history pagination; full match details must verify game identity, timing, and recorded teammates. If a newer game replaces the target before indexing, the target stays unverified rather than importing another game. Non-rate-limit failures retry after sixty seconds; provider cooldowns still apply. Late verified game logs publish before the previous unverified session summary is removed and replaced by updated totals at the bottom.
