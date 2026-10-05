# Operations

Start `.venv\Scripts\python.exe -m rionnag` from the workspace. bot.py is a compatibility launcher. Run one process; the second fails explicitly. Generated state is in data/ and logs/ and never belongs in public Git.

Rivals stats require rivals-api 5.1 or later. Normal summary-based calculation is the SDK default. `/lookup` accepts an optional method choice: Normal or Precise. Omitted method is not passed to the SDK; Precise traverses available match history and details and can take several minutes. Browser fallback is enabled by default for profile lookups; install `rivals-api[browser]` and run `python -m camoufox fetch`. Set `RIVALS_API_BROWSER_FALLBACK=false` to opt out. A live A G N 1 normal lookup recovered all three stat categories through Tracker's browser fallback on October 4; provider coverage remains uncertain and RivalsData still returned a genuine rate limit. HTTP 429 cooldowns are respected rather than bypassed with a browser. The bot requests three-second minimum gaps per provider and a sixty-second fallback cooldown; adjust `RIVALS_API_REQUEST_INTERVAL` and `RIVALS_API_RATE_LIMIT_COOLDOWN` if needed. Server Retry-After headers override the fallback cooldown. Availability is not guaranteed. All three win-rate calls explicitly use season="current"; unavailable current-season data is reported without substituting all-season totals. Precise hero attribution can require one detail read per tracked match. Application stats use normal calculations and retain their five-minute timeout while interactions remain deferred. Scrim monitoring retains its existing browser-disabled request pacing.

## Form changes

1. Edit config/forms.json, preserving stable existing question keys and adding unique keys for new questions.
2. Increase the game's version integer. Changes without version increases and version decreases are rejected before mutation.
3. Test and commit. Use owner-only `/reload_forms` or restart. Outdated members, including the owner, lose assignable roles except Rionnag and receive private tickets with cached answers.
4. Accepted returners complete current required questions and get automatic role restoration. Pending/rejected/new applicants need manager review.

Do not delete the database for routine form changes: it contains restoration snapshots. `/onboard member` repairs state without intentionally invalidating completed forms.

## Commands and recovery

Member departure permanently deletes their saved onboarding/profile data and private ticket. Startup repair catches departures missed offline. Rejoining requires a fresh form; previous roles and answers are not restored.

`/edit_form` is available to everyone and opens their own saved game form with prefilled answers. Finish every page and availability to save. Completed membership roles stay unchanged; pending applications update in place. Members without saved game data finish onboarding first.

`/profile` privately shows your saved current-version game form by default; the optional member argument shows another member's profile. It includes their in-game username, configured form fields, current time as plain clock text in the profile owner's saved time zone (at command invocation), and free days/time windows as Discord time-only timestamps. Availability is calculated from your selected time zone and upcoming weekdays, including overnight endings; Discord renders timestamps in each viewer's local time zone. It follows the saved game's definition without a game selector; unfinished/reset members finish their form first. `/lookup` remains the separate account stats search.

`/lookup query` shows account stats and requires a Discord username/display name, pasted mention, or Marvel Rivals username. Autocomplete prioritizes saved members, displaying their saved game username and Discord username without membership labels. Queries of at least two characters also use the form's public account search alongside saved-member matches. Account searches run independently of slow stats reads, with at most three concurrent searches and a short cache. Slow/unavailable search retains saved-member suggestions; typed names can still be submitted. Ambiguous member names require selecting a suggestion. Saved members retain their profile fields; external accounts show public stats only. `/promote` lets game managers promote tryout → team → manager. `/reload_forms` and `/onboard` are owner-only. Existing `/scrim` controls configure lobbies and voice rooms. `/scrim_opportunities` previews collector offers for Marvel managers.

The scrim channel configuration was restored after the deliberate wipe, reusing the existing scrim-control message, Scrim Waiting Room, and Scrim voice channel. Previous matches, queues, and feeds remain deleted. `/scrim setup` can change the mapping; `scripts/repair_scrim_channels.py` recovers these known channels only when configuration is missing and refuses to replace existing settings. Restart refreshes the saved dashboard with current voice attendance. The optional collector needs its own environment/private .env. Its default feed is ../data/scrim_feed.sqlite3 relative to its folder.

Automatic matched offers go to channel 1555989494451146802. Keep the collector on the default shared feed path; the bot checks every sixty seconds. It needs View Channel, Read Message History, Send Messages, and Embed Links there. Only accepted Marvel Rivals players on the current form with a live tryout/team/manager role count or vote. At least four must be available for the full advertised interval; start-only posts assume one hour. Dates/times display in each viewer's Discord time zone. New posts ping every matching player in content; updates and votes do not re-ping. Green Vote/red Remove vote update the saved Voted x/6 roster, with six unique voters maximum. Posts remain through their duration and are deleted at the end, when their source disappears, or when they no longer match four players. Refresh timing can delay an update/deletion by up to one minute. Completing or editing a form updates eligibility on the next refresh; no form version change or membership reset is needed. Voting does not confirm a booking.

Owner/Marvel managers can set the shared default opponent-rank search with `/scrim_rank min_rank:GM` (Grandmaster only) or `/scrim_rank min_rank:Diamond max_rank:Grandmaster`. Opponent ranges qualify only when their entire range is within the selected range; an omitted maximum uses the minimum tier, including all its divisions. Division-specific searches such as GM I are supported. `/scrim_rank min_rank:Any` clears the filter. Settings survive restart and affect both the automatic channel and `/scrim_opportunities`; updates immediately refresh the channel. Future offers are labeled Upcoming and started offers In progress, with Discord relative times.

Opportunity titles say Scrim Found!; the date/time details beneath them render in the viewer's local time. Player mentions remain in message content, without a duplicate Available players field. Every new post pings matching players, including offers returning after a filter change. Existing posts and vote counts are edited without notifications. Filter changes immediately delete nonmatching posts and add newly matching offers. End-time expiry still removes posts within the next refresh.

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
