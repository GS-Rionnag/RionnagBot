"""One serialized, restart-safe monitor for all Marvel Rivals scrim sessions."""

from __future__ import annotations

import asyncio
import logging
import os
import time

from rionnag.scrims import scrim_rivals
from rionnag.scrims.scrim_results import ScrimResultLogs

logger = logging.getLogger(__name__)


class ScrimMonitor:
    def __init__(self, controller):
        self.controller = controller
        self.interval = max(5, float(os.getenv("SCRIM_POLL_INTERVAL", "5")))
        self.budget = scrim_rivals.RequestBudget()
        self.task = None
        self.cursor = 0
        self.results = ScrimResultLogs(controller)

    def start(self):
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self.run(), name="scrim-rivals-monitor")

    async def run(self):
        while not self.controller.bot.is_closed():
            try:
                await self.results.tick()
                await self.tick()
            except Exception:
                logger.exception("Scrim monitoring failed; saved sessions retained")
            await asyncio.sleep(self.interval)

    async def tick(self):
        await self.controller.end_empty_sessions()
        store = self.controller.store
        jobs = [("live", s["id"], None) for s in store.active() if self.supported(s)]
        jobs += [
            ("history", m["session_id"], m)
            for m in store.pending_matches()
            if m["status"] == "completed" and self.supported(store.get(m["session_id"]))
        ]
        now = time.time()
        for offset in range(len(jobs)):
            index = (self.cursor + offset) % len(jobs)
            kind, sid, match = jobs[index]
            session = store.get(sid)
            state = self.state(session, kind, match)
            if state.get("next_at", 0) > now:
                continue
            self.cursor = (index + 1) % len(jobs)
            await self.probe(kind, session, match, freshness=max(600, len(jobs) * self.interval * 18))
            return

    @staticmethod
    def supported(session):
        return session["game"].casefold() == "marvel rivals" and any(
            not session["players"][mid].get("simulated") for mid in session["roster"]
        )

    @staticmethod
    def state(session, kind, match=None):
        if kind == "live":
            return session.setdefault("detection", {})
        return session.setdefault("imports", {}).setdefault(str(match["id"]), {})

    async def probe(self, kind, session, match=None, *, freshness=600):
        store = self.controller.store
        guild = self.controller.bot.get_guild(session["guild_id"])
        if guild is None:
            return
        state = self.state(session, kind, match)
        probe = state.get("probe", 0)
        roster = match["roster"] if match else [session["players"][mid] for mid in session["roster"]]
        # Demo teammates are unrelated leaderboard accounts; probe real starters only.
        if session.get("test_mode"):
            roster = [person for person in roster if not person.get("simulated")]
        if not roster:
            return
        person = roster[probe % len(roster)]
        # API I/O is outside the guild lock; controls and voice events stay responsive.
        error = None
        try:
            if kind == "live":
                result = await asyncio.to_thread(scrim_rivals.check_live, person, self.budget)
            else:
                result = await asyncio.to_thread(
                    scrim_rivals.recent_result,
                    {**match, "test_mode": bool(session.get("test_mode"))},
                    probe,
                    store.used_ids(guild.id),
                    self.budget,
                    max(300, freshness / 2),
                )
        except Exception as exc:
            result, error = None, exc
        async with self.controller.lock(guild.id):
            current = store.get(session["id"])
            # Discard responses obtained for an ended session or a changed lineup/game.
            if kind == "live" and (
                current["status"] != session["status"]
                or current.get("revision", 0) != session.get("revision", 0)
                or current["roster"] != session["roster"]
                or current["match_id"] != session["match_id"]
            ):
                return
            state = self.state(current, kind, match)
            state["probe"] = probe + 1
            state["next_at"] = time.time() + (self.interval if kind == "live" else 15)
            if error:
                state["errors"] = min(state.get("errors", 0) + 1, 6)
                delay = min(900, 30 * 2 ** state["errors"])
                if isinstance(error, scrim_rivals.MonitorRateLimit):
                    delay = max(delay, error.delay)
                elif kind == "history":
                    # A failed account lookup must not stall the other recorded starters.
                    delay = 15
                state["next_at"] = time.time() + delay
                if kind == "live":
                    state.setdefault("absent", {}).pop(str(person["member_id"]), None)
                current["monitor_notice"] = "Rivals Data is unavailable; detection will retry automatically."
                store.save(current)
                logger.warning("Scrim %s %s probe failed: %s", current["id"], kind, type(error).__name__)
                await self.update_panel(guild, current)
                return
            state["errors"] = 0
            current.pop("monitor_notice", None)
            if kind == "history":
                if result:
                    external_id, payload = result
                    store.snapshot(match["id"], "details", payload, external_id)
                    current["result_notice"] = (
                        f"Game {match['number']} ended and recorded as Rivals match {external_id}."
                    )
                    state["verified"] = external_id
                    if current["status"] == "prepared" and current["number"] == match["number"] + 1:
                        current["note"] = "Game Ended · Match statistics verified and recorded."
                else:
                    current["result_notice"] = (
                        f"Game {match['number']} ended. "
                        "Waiting for a recent custom match with the recorded real starters. "
                        "Results retry for one hour; unavailable stats remain unverified in /scrim log."
                    )
                store.save(current)
                await self.update_panel(guild, current)
                return
            mid = person["member_id"]
            current["players"][mid]["uid"] = result["uid"]
            store.save(current)
            if current["status"] == "prepared":
                battle_id = result["battle_id"]
                blocked = set(current.get("finished_live_ids", [])) | store.used_ids(guild.id)
                if result["custom"] is True and (not battle_id or battle_id not in blocked):
                    await self.controller.game_started(guild, current, result)
                elif result["custom"] is None:
                    current["monitor_notice"] = (
                        "Waiting for Rivals Data to expose an explicit custom-game status."
                    )
                    store.save(current)
                    await self.update_panel(guild, current)
                return
            live_id = state.get("battle_id")
            # A manually started game acquires its live identity on the first confirmed probe.
            if not live_id and result["custom"] is True and result["battle_id"]:
                live_id = state["battle_id"] = result["battle_id"]
            # An opaque battle ID still proves the original game is running even without mode metadata.
            running = bool(live_id and result["battle_id"] == live_id) or (
                result["custom"] is True and (not live_id or not result["battle_id"])
            )
            absent = state.setdefault("absent", {})
            left_tracked = bool(live_id and result["battle_id"] and result["battle_id"] != live_id)
            if running or (result["custom"] is None and not left_tracked):
                absent.pop(str(mid), None)
            else:
                previous = absent.get(str(mid), {})
                count = previous.get("count", 0) if time.time() - previous.get("at", 0) <= freshness else 0
                absent[str(mid)] = {"count": count + 1, "at": time.time()}
            store.save(current)
            if absent.get(str(mid), {}).get("count", 0) >= 1:
                await self.controller.game_ended(guild, current)

    async def update_panel(self, guild, data):
        lobby = self.controller.store.lobby(guild.id, data["game"])
        if lobby["session_id"] == data["id"]:
            await self.controller.panel(lobby, guild, data)
