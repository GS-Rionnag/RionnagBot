# Operations

Start `.venv\Scripts\python.exe -m rionnag` from the workspace. bot.py is a compatibility launcher. Run one process; the second fails explicitly. Generated state is in data/ and logs/ and never belongs in public Git.

Rivals stats require rivals-api 5.1 or later. Normal summary-based calculation is the SDK default. `/lookup` accepts an optional method choice: Normal or Precise. Omitted method is not passed to the SDK; Precise traverses available match history and details and can take several minutes. Browser fallback is enabled by default for profile lookups; install `rivals-api[browser]` and run `python -m camoufox fetch`. Set `RIVALS_API_BROWSER_FALLBACK=false` to opt out. A live A G N 1 normal lookup recovered all three stat categories through Tracker's browser fallback on October 4; provider coverage remains uncertain and RivalsData still returned a genuine rate limit. HTTP 429 cooldowns are respected rather than bypassed with a browser. Availability is not guaranteed. All three win-rate calls explicitly use season="current"; unavailable current-season data is reported without substituting all-season totals. Precise hero attribution can require one detail read per tracked match. Application stats use normal calculations and retain their five-minute timeout while interactions remain deferred. Scrim monitoring retains its existing browser-disabled request pacing.

## Form changes

1. Edit config/forms.json, preserving stable existing question keys and adding unique keys for new questions.
2. Increase the game's version integer. Changes without version increases and version decreases are rejected before mutation.
3. Test and commit. Use owner-only `/reload_forms` or restart. Outdated members, including the owner, lose assignable roles except Rionnag and receive private tickets with cached answers.
4. Accepted returners complete current required questions and get automatic role restoration. Pending/rejected/new applicants need manager review.

Do not delete the database for routine form changes: it contains restoration snapshots. `/onboard member` repairs state without intentionally invalidating completed forms.

## Commands and recovery

Member departure permanently deletes their saved onboarding/profile data and private ticket. Startup repair catches departures missed offline. Rejoining requires a fresh form; previous roles and answers are not restored.

`/edit_form` is available to everyone and opens their own saved game form with prefilled answers. Finish every page and availability to save. Completed membership roles stay unchanged; pending applications update in place. Members without saved game data finish onboarding first.

`/profile` privately shows your saved current-version game form by default; the optional member argument shows another member's profile. It includes their in-game username, configured form fields, current time (at command invocation), and free days/time windows as Discord time-only timestamps. Availability is calculated from your selected time zone and upcoming weekdays, including overnight endings; Discord renders timestamps in each viewer's local time zone. It follows the saved game's definition without a game selector; unfinished/reset members finish their form first. `/lookup` remains the separate account stats search.

`/lookup query` shows account stats and requires a Discord username/display name, pasted mention, or Marvel Rivals username. Autocomplete prioritizes saved members, displaying their saved game username and Discord username without membership labels. Queries of at least two characters also use the form's public account search alongside saved-member matches. Account searches run independently of slow stats reads, with at most three concurrent searches and a short cache. Slow/unavailable search retains saved-member suggestions; typed names can still be submitted. Ambiguous member names require selecting a suggestion. Saved members retain their profile fields; external accounts show public stats only. `/promote` lets game managers promote tryout → team → manager. `/reload_forms` and `/onboard` are owner-only. Existing `/scrim` controls configure lobbies and voice rooms. `/scrim_opportunities` previews collector offers for Marvel managers.

The scrim channel configuration was restored after the deliberate wipe, reusing the existing scrim-control message, Scrim Waiting Room, and Scrim voice channel. Previous matches, queues, and feeds remain deleted. `/scrim setup` can change the mapping; `scripts/repair_scrim_channels.py` recovers these known channels only when configuration is missing and refuses to replace existing settings. Restart refreshes the saved dashboard with current voice attendance. The optional collector needs its own environment/private .env. Its default feed is ../data/scrim_feed.sqlite3 relative to its folder.

Role failures: place the bot above all membership roles, below Rionnag. Check configured IDs/logs. Failed reconciliation retries every five minutes. Completed members never receive new tickets solely due to restart. Closed DMs retain a retry notification and post the result in the ticket. Resume forms through ticket buttons.

Inspect staged paths before public pushes. Exclude .env, data/, logs/, backups, transcripts, and provider caches. Future agents must commit/push coherent minor/major changes and maintain the timeline.

The organization uses discord.py's [cogs](https://discordpy.readthedocs.io/en/latest/ext/commands/cogs.html) and [persistent view registration](https://github.com/Rapptz/discord.py/blob/master/examples/views/persistent.py).
