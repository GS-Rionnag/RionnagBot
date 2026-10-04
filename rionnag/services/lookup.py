"""Resolve public game accounts and saved guild profiles without reading form answers."""

import asyncio
import re

from rionnag.integrations.rivals import queued_lookup, search_player_accounts


class Lookup:
    def __init__(self, service):
        self.service = service
        self.search_task = None
        self.search_query = None

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
        if local:
            return [(label[:100], f"member:{member.id}") for _, label, member in local[:25]]
        query = query.strip()
        if len(query) < 2:
            return []
        # Keep slow provider searches from accumulating behind the shared provider queue.
        if self.search_task is None or (self.search_task.done() and query != self.search_query):
            self.search_query = query
            self.search_task = asyncio.create_task(asyncio.to_thread(
                queued_lookup, search_player_accounts, query
            ))
        if query != self.search_query:
            return []
        try:
            accounts = await asyncio.wait_for(asyncio.shield(self.search_task), timeout=1.5)
        except Exception:
            return []
        return [(f"{row['name']} · {row['uid']}"[:100], f"account:{row['uid']}") for row in accounts[:25]]

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
