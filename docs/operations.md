# Operations

Start `.venv\Scripts\python.exe -m rionnag` from the workspace. bot.py is a compatibility launcher. Run one process; the second fails explicitly. Generated state is in data/ and logs/ and never belongs in public Git.

Rivals stats require rivals-api 4.1 or later. Browser fallback remains optional (`rivals-api[browser]` plus `python -m camoufox fetch`); it did not resolve Tracker's current-season network block during the migration check. All three win-rate calls explicitly use season="current"; unavailable current-season data is reported without substituting all-season totals. Canonical hero attribution can require one detail read per tracked match, so application lookup allows up to five minutes while Discord interactions remain deferred. Scrim monitoring retains its existing browser-disabled request pacing.

## Form changes

1. Edit config/forms.json, preserving stable existing question keys and adding unique keys for new questions.
2. Increase the game's version integer. Changes without version increases and version decreases are rejected before mutation.
3. Test and commit. Use owner-only `/reload_forms` or restart. Outdated members, including the owner, lose assignable roles except Rionnag and receive private tickets with cached answers.
4. Accepted returners complete current required questions and get automatic role restoration. Pending/rejected/new applicants need manager review.

Do not delete the database for routine form changes: it contains restoration snapshots. `/onboard member` repairs state without intentionally invalidating completed forms.

## Commands and recovery

`/edit_form` is available to everyone and opens their own saved game form with prefilled answers. Finish every page and availability to save. Completed membership roles stay unchanged; pending applications update in place. Members without saved game data finish onboarding first.

`/profile` shows saved Marvel Rivals data/stats. `/promote` lets game managers promote tryout → team → manager. `/reload_forms` and `/onboard` are owner-only. Existing `/scrim` controls configure lobbies and voice rooms. `/scrim_opportunities` previews collector offers for Marvel managers.

The scrim channel configuration was restored after the deliberate wipe, reusing the existing scrim-control message, Scrim Waiting Room, and Scrim voice channel. Previous matches, queues, and feeds remain deleted. `/scrim setup` can change the mapping; `scripts/repair_scrim_channels.py` recovers these known channels only when configuration is missing and refuses to replace existing settings. Restart refreshes the saved dashboard with current voice attendance. The optional collector needs its own environment/private .env. Its default feed is ../data/scrim_feed.sqlite3 relative to its folder.

Role failures: place the bot above all membership roles, below Rionnag. Check configured IDs/logs. Failed reconciliation retries every five minutes. Completed members never receive new tickets solely due to restart. Closed DMs retain a retry notification and post the result in the ticket. Resume forms through ticket buttons.

Inspect staged paths before public pushes. Exclude .env, data/, logs/, backups, transcripts, and provider caches. Future agents must commit/push coherent minor/major changes and maintain the timeline.

The organization uses discord.py's [cogs](https://discordpy.readthedocs.io/en/latest/ext/commands/cogs.html) and [persistent view registration](https://github.com/Rapptz/discord.py/blob/master/examples/views/persistent.py).
