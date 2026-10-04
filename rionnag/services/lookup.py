"""Resolve public game accounts and saved guild profiles without reading form answers."""

import asyncio
import re
import time

from rionnag.integrations.rivals import search_player_accounts


class Lookup:
    def __init__(self, service):
        self.service = service
        self.searches = {}

    def matches(self, guild, query):
        query = query.strip().casefold()
        names = self.service.store.profile_names(guild.id)
        matches = []
        for member in guild.members:
            if member.id not in names:
                continue
            aliases = [names[member.id], member.name, member.display_name, member.global_name or ""]
            aliases = [name.casefold() for name in aliases if name]
            if query and not any(query in name for name in aliases):
                continue
            rank = 0 if query in aliases else 1 if any(name.startswith(query) for name in aliases) else 2
            label = f"{names[member.id]} · @{member.name}"
            matches.append((rank, label, member))
        return sorted(matches, key=lambda row: (row[0], row[1].casefold(), row[2].id))

    async def autocomplete(self, guild, query):
        local = self.matches(guild, query)
        choices = [(label[:100], f"member:{member.id}") for _, label, member in local[:25]]
        query = query.strip()
        if len(query) < 2:
            return choices
        key = query.casefold()
        now = time.monotonic()
        self.searches = {key: entry for key, entry in self.searches.items()
                         if not entry[1].done() or now - entry[0] < 30}
        if key not in self.searches:
            # Search must not wait behind minute-long stats reads. Bound independent searches.
            if sum(not task.done() for _, task in self.searches.values()) >= 3:
                return choices
            if len(self.searches) >= 32:
                completed = next((key for key, (_, task) in self.searches.items() if task.done()), None)
                if completed is not None:
                    del self.searches[completed]
            task = asyncio.create_task(asyncio.to_thread(search_player_accounts, query))
            # Retrieve errors even if every waiting autocomplete has already timed out.
            task.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
            self.searches[key] = (now, task)
        try:
            accounts = await asyncio.wait_for(asyncio.shield(self.searches[key][1]), timeout=2.0)
        except Exception:
            return choices
        seen = set()
        for row in accounts:
            uid = str(row["uid"])
            if uid in seen:
                continue
            seen.add(uid)
            choices.append((str(row["name"])[:100], f"account:{uid}"))
        return choices[:25]

    def resolve(self, guild, query):
        query = query.strip()
        if not query:
            raise ValueError("Enter a member or Marvel Rivals username.")
        mention = re.fullmatch(r"<@!?(\d+)>|member:(\d+)", query)
        if mention:
            member = guild.get_member(int(mention.group(1) or mention.group(2)))
            saved = self.service.store.saved_profile(guild.id, member.id) if member else None
            if not saved:
                raise ValueError("This member has no completed game profile.")
            account = self.service.store.saved_uid(guild.id, member.id, "Marvel Rivals") or saved[0]
            return member, saved, account
        if query.startswith("account:"):
            uid = query.removeprefix("account:")
            if not uid.isdigit():
                raise ValueError("Choose a valid account from autocomplete.")
            return None, None, uid
        matches = self.matches(guild, query)
        if matches:
            best = [row for row in matches if row[0] == matches[0][0]]
            if len(best) > 1:
                raise ValueError("Multiple members match. Choose one from autocomplete.")
            return self.resolve(guild, f"member:{best[0][2].id}")
        return None, None, query
