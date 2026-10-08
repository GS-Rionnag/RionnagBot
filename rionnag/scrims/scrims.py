"""Durable scrim state and deterministic, role-preserving rotation."""

from __future__ import annotations

import json
import random
import sqlite3
import time
from dataclasses import asdict, dataclass, replace
from itertools import product

ROLES = ("Tank", "DPS", "Support")
ALIASES = {
    "vanguard": "Tank",
    "tank": "Tank",
    "duelist": "DPS",
    "dps": "DPS",
    "strategist": "Support",
    "support": "Support",
}


@dataclass
class Player:
    member_id: int
    username: str
    roles: tuple[str, ...]
    uid: str | None = None
    played: int = 0
    last_played: int = 0
    joined: int = 0
    simulated: bool = False
    benched_at: int = 0


def normalized_roles(values) -> tuple[str, ...]:
    return tuple(dict.fromkeys(ALIASES[v.casefold()] for v in values if v and v.casefold() in ALIASES))


def make_team(
    players: list[Player],
    current: dict[int, str] | None = None,
    replacements: int | None = None,
    *,
    required_ids=(),
    prefer_real=False,
) -> dict[int, str]:
    """Choose six eligible players; incumbents keep their roles during rotations.

    Dynamic programming avoids greedy assignments that strand flexible players.
    Main-role fit wins, followed by completed-match counts and longest wait.
    With replacements specified, exactly that many newcomers must be selected.
    """
    current = current or {}
    states = {(0, 0, 0, 0): ((0, 0, 0, 0, 0), {})}
    for player in sorted(players, key=lambda p: (p.joined, p.member_id)):
        next_states = {} if player.member_id in required_ids else dict(states)
        options = (current[player.member_id],) if player.member_id in current else player.roles
        for key, (score, team) in states.items():
            for role in options:
                if role not in player.roles:
                    continue
                index = ROLES.index(role)
                counts = list(key)
                counts[index] += 1
                counts[3] += int(player.member_id not in current)
                if counts[index] > 2 or (replacements is not None and counts[3] > replacements):
                    continue
                new_score = tuple(
                    a + b
                    for a, b in zip(
                        score,
                        (
                            int(prefer_real and player.simulated),
                            player.roles.index(role),
                            player.played,
                            player.last_played,
                            player.joined,
                        ),
                        strict=True,
                    )
                )
                new_key = tuple(counts)
                if new_key not in next_states or new_score < next_states[new_key][0]:
                    next_states[new_key] = (new_score, {**team, player.member_id: role})
        states = next_states
    possible = [
        value
        for key, value in states.items()
        if key[:3] == (2, 2, 2) and (replacements is None or key[3] == replacements)
    ]
    if not possible:
        raise ValueError(
            "Need six eligible players who can fill 2 Tank, 2 DPS, and 2 Support slots. "
            "Join the waiting VC and save roles with /edit_profile."
        )
    return min(possible, key=lambda value: value[0])[1]


def rotate_team(players: list[Player], current: dict[int, str], count: int) -> dict[int, str]:
    if count not in (0, 1, 2):
        raise ValueError("Choose 0, 1, or 2 substitutions.")
    # Prefer the requested count; never force an off-role replacement.
    for actual in range(count, -1, -1):
        try:
            return make_team(players, current, actual)
        except ValueError:
            continue
    raise ValueError(
        "A selected player left and the waiting room has no compatible replacement. "
        "Add players, then use Refresh lineup."
    )


def random_team(players: list[Player], *, required_ids=(), prefer_real=False) -> dict[int, str]:
    """Shuffle initial ties without changing the durable queue order."""
    shuffled = random.sample(players, len(players))
    return make_team(
        [replace(p, joined=i) for i, p in enumerate(shuffled)],
        required_ids=required_ids,
        prefer_real=prefer_real,
    )


def reroll_team(players: list[Player], *, prefer_real=False) -> dict[int, str]:
    """Explicit manager override: redraw everyone without changing durable counts."""
    shuffled = random.sample(players, len(players))
    candidates = [replace(p, played=0, last_played=0, joined=i) for i, p in enumerate(shuffled)]
    return make_team(candidates, prefer_real=prefer_real)


def substitute_one(players, current, history, protected=()):
    """Replace one compatible starter, prioritizing absences and least-used bench."""
    present = {p.member_id: p for p in players}
    options = []
    for incoming in players:
        if incoming.member_id in current:
            continue
        for outgoing, role in current.items():
            if role not in incoming.roles or (outgoing in protected and outgoing in present):
                continue
            old = history[outgoing]
            old_roles = tuple(old["roles"])
            old_preference = old_roles.index(role) if role in old_roles else 1
            main_delta = incoming.roles.index(role) - old_preference
            if outgoing in present:
                if main_delta > 0:
                    continue  # Do not bench a main-role player for an off-main swap.
                if main_delta == 0 and incoming.played > old["played"]:
                    continue  # Equal counts are eligible; avoid favoring more-used players.
            score = (
                outgoing in present,
                main_delta,
                incoming.roles.index(role),
                incoming.played,
                incoming.last_played,
                incoming.benched_at,
                -old["played"],
                -old["last_played"],
                incoming.joined,
                incoming.member_id,
                outgoing,
            )
            options.append((score, incoming.member_id, outgoing, role))
    if not options:
        raise ValueError(
            "No eligible role-compatible substitute is available. "
            "Players subbed in this round must play before automatic rotation; managers can use Edit lineup."
        )
    _, incoming, outgoing, role = min(options)
    roster = {mid: r for mid, r in current.items() if mid != outgoing}
    roster[incoming] = role
    return roster, incoming, outgoing


def manual_lineup(players, current, history, operation, first, second=None):
    """Manager override with a complete 2/2/2 roster and mandatory in/out choices.

    Force in/out may select the other player automatically and reshuffle roles,
    including an off-role assignment when required. Swap still validates saved
    role compatibility. Counts never change.
    """
    present = {p.member_id: p for p in players}
    roster = dict(current)

    def roles(mid):
        return present[mid].roles if mid in present else history.get(mid, {}).get("roles", ())

    def replace_slot(outgoing, incoming, *, force=False):
        nonlocal roster
        if outgoing not in roster or incoming in roster or incoming not in present:
            raise ValueError("Choose an active player to take out and an available substitute to bring in.")
        role = roster[outgoing]
        if role not in roles(incoming):
            if not force:
                raise ValueError(f"The incoming player must have {role} in their saved preferred roles.")
            selected = [mid for mid in roster if mid != outgoing] + [incoming]
            candidates = []
            for assigned in product(ROLES, repeat=6):
                if any(assigned.count(r) != 2 for r in ROLES):
                    continue
                score = (
                    sum(r not in roles(mid) for mid, r in zip(selected, assigned, strict=True)),
                    sum(mid in roster and roster[mid] != r
                        for mid, r in zip(selected, assigned, strict=True)),
                    sum(roles(mid).index(r) if r in roles(mid) else len(ROLES)
                        for mid, r in zip(selected, assigned, strict=True)),
                    assigned,
                )
                candidates.append((score, dict(zip(selected, assigned, strict=True))))
            roster = min(candidates, key=lambda item: item[0])[1]
            return
        del roster[outgoing]
        roster[incoming] = role

    if operation == "in":
        if first in roster or first not in present:
            raise ValueError("Force in requires an available substitute who is not already in the lineup.")
        if second is None:
            options = [mid for mid, role in roster.items() if role in roles(first)]
            if not options:
                options = list(roster)
            second = min(
                options,
                key=lambda mid: (
                    roles(first).index(roster[mid]) if roster[mid] in roles(first) else len(ROLES),
                    mid in present,
                    -history[mid]["played"],
                    -history[mid]["last_played"],
                    mid,
                ),
            )
        replace_slot(second, first, force=True)
    elif operation == "out":
        if first not in roster:
            raise ValueError("Force out requires a player from the active lineup.")
        if second is None:
            options = [p for p in players if p.member_id not in roster and roster[first] in p.roles]
            if not options:
                options = [p for p in players if p.member_id not in roster]
            if not options:
                raise ValueError("No substitute is available. Add one before forcing this player out.")
            second = min(
                options,
                key=lambda p: (
                    p.roles.index(roster[first]) if roster[first] in p.roles else len(ROLES),
                    p.played,
                    p.last_played,
                    p.benched_at,
                    p.joined,
                    p.member_id,
                ),
            ).member_id
        replace_slot(first, second, force=True)
    elif operation == "swap":
        if second is None or first == second:
            raise ValueError("Choose two different players for a swap.")
        if first in roster and second in roster:
            a, b = roster[first], roster[second]
            if a == b:
                raise ValueError(
                    "Both players already have the same role. Choose different roles or a substitute."
                )
            if b not in roles(first) or a not in roles(second):
                raise ValueError("Both players must have each other’s assigned role saved to swap roles.")
            roster[first], roster[second] = b, a
        elif first in roster:
            replace_slot(first, second)
        elif second in roster:
            replace_slot(second, first)
        else:
            raise ValueError("At least one player must be in the active lineup.")
    else:
        raise ValueError("Choose Force in, Force out, or Swap two players.")
    if len(roster) != 6 or any(list(roster.values()).count(role) != 2 for role in ROLES):
        raise ValueError("The resulting lineup must have two Tank, two DPS, and two Support players.")
    return roster


def reconcile_lobby(data, member_ids):
    """Membership changes invalidate stale manager buttons; leaving clears readiness."""
    ids = sorted(set(member_ids))
    if ids != data["queue_ids"]:
        data["ready_ids"] = sorted(set(data["ready_ids"]) & set(ids))
        data["queue_ids"] = ids
        data["revision"] += 1


def all_ready(data):
    return len(data["queue_ids"]) >= 6 and set(data["queue_ids"]) == set(data["ready_ids"])


class ScrimStore:
    def __init__(self, connection):
        self.connection = connection

    def initialize(self):
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS scrim_config (
                    guild_id INTEGER, game TEXT, control_id INTEGER NOT NULL,
                    waiting_id INTEGER NOT NULL, stage_id INTEGER NOT NULL,
                    PRIMARY KEY(guild_id, game));
                CREATE TABLE IF NOT EXISTS scrim_lobbies (
                    id INTEGER PRIMARY KEY, guild_id INTEGER NOT NULL, game TEXT NOT NULL,
                    data TEXT NOT NULL, UNIQUE(guild_id, game));
                CREATE TABLE IF NOT EXISTS scrim_test_settings (
                    guild_id INTEGER, game TEXT, member_id INTEGER NOT NULL,
                    enabled INTEGER NOT NULL, pool TEXT NOT NULL,
                    PRIMARY KEY(guild_id, game));
                CREATE TABLE IF NOT EXISTS scrim_sessions (
                    id INTEGER PRIMARY KEY, guild_id INTEGER NOT NULL, game TEXT NOT NULL,
                    status TEXT NOT NULL, data TEXT NOT NULL);
                CREATE UNIQUE INDEX IF NOT EXISTS scrim_active_stage
                    ON scrim_sessions(guild_id, json_extract(data, '$.stage_id')) WHERE status != 'ended';
                CREATE TABLE IF NOT EXISTS scrim_matches (
                    id INTEGER PRIMARY KEY, session_id INTEGER NOT NULL, number INTEGER NOT NULL,
                    status TEXT NOT NULL, started_at REAL NOT NULL, ended_at REAL,
                    roster TEXT NOT NULL, external_id TEXT, data TEXT NOT NULL DEFAULT '{}',
                    UNIQUE(session_id, number));
                CREATE UNIQUE INDEX IF NOT EXISTS scrim_external_match
                    ON scrim_matches(session_id, external_id) WHERE external_id IS NOT NULL;
                CREATE TABLE IF NOT EXISTS scrim_snapshots (
                    id INTEGER PRIMARY KEY, match_id INTEGER NOT NULL, captured_at REAL NOT NULL,
                    kind TEXT NOT NULL, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS scrim_events (
                    id INTEGER PRIMARY KEY, session_id INTEGER NOT NULL, created_at REAL NOT NULL,
                    actor_id INTEGER, action TEXT NOT NULL, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS scrim_result_deliveries (
                    match_id INTEGER PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS scrim_session_deliveries (
                    session_id INTEGER PRIMARY KEY, data TEXT NOT NULL);
            """)

    def configure(self, guild_id, game, control_id, waiting_id, stage_id):
        with self.connection() as db:
            db.execute(
                "INSERT OR REPLACE INTO scrim_config VALUES (?, ?, ?, ?, ?)",
                (guild_id, game, control_id, waiting_id, stage_id),
            )

    def config(self, guild_id, game):
        with self.connection() as db:
            row = db.execute(
                "SELECT control_id, waiting_id, stage_id FROM scrim_config WHERE guild_id=? AND game=?",
                (guild_id, game),
            ).fetchone()
        if not row:
            raise ValueError("Run /scrim setup first.")
        return dict(zip(("control_id", "waiting_id", "stage_id"), row, strict=True))

    def configured(self):
        with self.connection() as db:
            return list(db.execute("SELECT guild_id,game FROM scrim_config"))

    def lobby(self, guild_id, game):
        with self.connection() as db:
            row = db.execute(
                "SELECT id FROM scrim_lobbies WHERE guild_id=? AND game=?", (guild_id, game)
            ).fetchone()
            if row:
                return self.get_lobby(row[0])
            data = {
                **self.config(guild_id, game),
                "guild_id": guild_id,
                "game": game,
                "queue_ids": [],
                "ready_ids": [],
                "revision": 0,
                "message_id": None,
                "session_id": None,
                "notice": "",
            }
            cursor = db.execute(
                "INSERT INTO scrim_lobbies(guild_id,game,data) VALUES (?,?,?)",
                (guild_id, game, json.dumps(data)),
            )
            data["id"] = cursor.lastrowid
            db.execute("UPDATE scrim_lobbies SET data=? WHERE id=?", (json.dumps(data), data["id"]))
        return data

    def get_lobby(self, lid):
        with self.connection() as db:
            row = db.execute("SELECT data FROM scrim_lobbies WHERE id=?", (lid,)).fetchone()
        if not row:
            raise ValueError("Scrim controls not found. Use /scrim status.")
        return json.loads(row[0])

    def save_lobby(self, data):
        with self.connection() as db:
            db.execute("UPDATE scrim_lobbies SET data=? WHERE id=?", (json.dumps(data), data["id"]))

    def event(self, data, actor, action, payload):
        with self.connection() as db:
            self._event(db, data["id"], actor, action, payload)

    def test_settings(self, guild_id, game):
        with self.connection() as db:
            row = db.execute(
                "SELECT member_id,enabled,pool FROM scrim_test_settings WHERE guild_id=? AND game=?",
                (guild_id, game),
            ).fetchone()
        return {"member_id": row[0], "enabled": bool(row[1]), "pool": json.loads(row[2])} if row else None

    def arm_test(self, guild_id, game, member_id, pool):
        with self.connection() as db:
            db.execute(
                "INSERT OR REPLACE INTO scrim_test_settings VALUES (?, ?, ?, 1, ?)",
                (guild_id, game, member_id, json.dumps(pool)),
            )

    def disable_test(self, guild_id, game):
        with self.connection() as db:
            db.execute(
                "UPDATE scrim_test_settings SET enabled=0 WHERE guild_id=? AND game=?", (guild_id, game)
            )

    def create(self, guild_id, game, config, players, roster, actor_id, *, test_mode=False):
        data = {
            **config,
            "guild_id": guild_id,
            "game": game,
            "status": "prepared",
            "number": 1,
            "roster": roster,
            "players": {p.member_id: asdict(p) for p in players},
            "message_id": None,
            "match_id": None,
            "note": "",
            "test_mode": test_mode,
            "test_host": actor_id if test_mode else None,
        }
        with self.connection() as db:
            cursor = db.execute(
                "INSERT INTO scrim_sessions(guild_id, game, status, data) VALUES (?, ?, ?, ?)",
                (guild_id, game, "prepared", json.dumps(data)),
            )
            data["id"] = cursor.lastrowid
            self._save(db, data)
            self._event(db, data["id"], actor_id, "created", roster)
        return data

    def get(self, session_id):
        with self.connection() as db:
            row = db.execute("SELECT data FROM scrim_sessions WHERE id=?", (session_id,)).fetchone()
        if not row:
            raise ValueError("Scrim session not found.")
        data = json.loads(row[0])
        data["roster"] = {int(k): v for k, v in data["roster"].items()}
        data["players"] = {int(k): v for k, v in data["players"].items()}
        return data

    def active(self):
        with self.connection() as db:
            ids = [row[0] for row in db.execute("SELECT id FROM scrim_sessions WHERE status != 'ended'")]
        return [self.get(sid) for sid in ids]

    def _save(self, db, data):
        db.execute(
            "UPDATE scrim_sessions SET status=?, data=? WHERE id=?",
            (data["status"], json.dumps(data), data["id"]),
        )

    def save(self, data):
        with self.connection() as db:
            self._save(db, data)

    def _event(self, db, sid, actor, action, payload):
        db.execute(
            "INSERT INTO scrim_events(session_id, created_at, actor_id, action, data) VALUES (?, ?, ?, ?, ?)",
            (sid, time.time(), actor, action, json.dumps(payload)),
        )

    def lineup(self, data, roster, actor, *, action="lineup"):
        before = data["roster"]
        data["roster"] = roster
        with self.connection() as db:
            self._save(db, data)
            self._event(db, data["id"], actor, action, {"before": before, "after": roster})

    def start(self, data, actor, *, started_at=None):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if self.get(data["id"])["status"] != "prepared":
                raise ValueError("This match has already started or the session has ended.")
            roster = [{**data["players"][mid], "role": role} for mid, role in data["roster"].items()]
            cursor = db.execute(
                "INSERT INTO scrim_matches(session_id, number, status, started_at, roster, data) "
                "VALUES (?, ?, 'playing', ?, ?, ?)",
                (
                    data["id"],
                    data["number"],
                    time.time() if started_at is None else started_at,
                    json.dumps(roster),
                    json.dumps({"detection": data.get("detection", {})}),
                ),
            )
            data.update(status="playing", match_id=cursor.lastrowid)
            self._save(db, data)
            self._event(db, data["id"], actor, "started", {"match_id": data["match_id"]})

    def finish(self, data, actor, aborted=False):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute(
                "UPDATE scrim_matches SET status=?, ended_at=? WHERE id=? AND status='playing'",
                ("aborted" if aborted else "completed", time.time(), data["match_id"]),
            )
            if changed.rowcount != 1:
                raise ValueError("This match was already finished.")
            if not aborted:
                for mid in data["roster"]:
                    data["players"][mid]["played"] += 1
                    data["players"][mid]["last_played"] = data["number"]
            data.update(status="prepared", number=data["number"] + 1, match_id=None)
            self._save(db, data)
            self._event(db, data["id"], actor, "aborted" if aborted else "finished", {})

    def match(self, match_id):
        with self.connection() as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT * FROM scrim_matches WHERE id=?", (match_id,)).fetchone()
        if row is None:
            raise ValueError("Match not found.")
        value = dict(row)
        value["roster"], value["data"] = json.loads(value["roster"]), json.loads(value["data"])
        return value

    def pending_matches(self, grace=900):
        with self.connection() as db:
            ids = [
                r[0]
                for r in db.execute(
                    "SELECT id FROM scrim_matches WHERE status='playing' OR "
                    "(status='completed' AND ended_at>? AND json_extract(data, '$.teams') IS NULL)",
                    (time.time() - grace,),
                )
            ]
        return [self.match(mid) for mid in ids]

    def session_matches(self, session_id):
        with self.connection() as db:
            ids = [
                r[0]
                for r in db.execute(
                    "SELECT id FROM scrim_matches WHERE session_id=? ORDER BY number",
                    (session_id,),
                )
            ]
        return [self.match(mid) for mid in ids]

    def pending_mute_restores(self, guild_id, member_id):
        with self.connection() as db:
            ids = [
                r[0]
                for r in db.execute(
                    "SELECT id FROM scrim_sessions WHERE guild_id=? AND status='ended' "
                    "AND json_extract(data, ?) IS NOT NULL ORDER BY id",
                    (guild_id, f'$.voice_mutes_before."{int(member_id)}"'),
                )
            ]
        return [self.get(sid) for sid in ids]

    def pending_results(self):
        with self.connection() as db:
            ids = [
                r[0]
                for r in db.execute(
                    "SELECT m.id FROM scrim_matches m LEFT JOIN scrim_result_deliveries d ON d.match_id=m.id "
                    "WHERE m.status='completed' AND m.external_id IS NOT NULL "
                    "AND json_extract(m.data, '$.teams') IS NOT NULL "
                    "AND (COALESCE(json_extract(d.data, '$.done'), 0)=0 "
                    "OR COALESCE(json_extract(d.data, '$.format_version'), 0)<7) ORDER BY m.id"
                )
            ]
        return [self.match(mid) for mid in ids]

    def result_delivery(self, match_id):
        with self.connection() as db:
            row = db.execute(
                "SELECT data FROM scrim_result_deliveries WHERE match_id=?",
                (match_id,),
            ).fetchone()
        return json.loads(row[0]) if row else {"message_ids": []}

    def save_result_delivery(self, match_id, data):
        with self.connection() as db:
            db.execute(
                "INSERT INTO scrim_result_deliveries VALUES (?, ?) "
                "ON CONFLICT(match_id) DO UPDATE SET data=excluded.data",
                (match_id, json.dumps(data)),
            )

    def pending_session_results(self):
        with self.connection() as db:
            ids = [
                row[0]
                for row in db.execute(
                    "SELECT s.id FROM scrim_sessions s LEFT JOIN scrim_session_deliveries d "
                    "ON d.session_id=s.id WHERE s.status='ended' "
                    "AND COALESCE(json_extract(d.data, '$.done'), 0)=0 "
                    "AND (json_extract(s.data, '$.ended_at') IS NOT NULL OR EXISTS "
                    "(SELECT 1 FROM scrim_matches m WHERE m.session_id=s.id AND m.status='completed' "
                    "AND m.external_id IS NOT NULL AND json_extract(m.data, '$.teams') IS NOT NULL)) "
                    "ORDER BY s.id"
                )
            ]
        return [self.get(sid) for sid in ids]

    def session_delivery(self, session_id):
        with self.connection() as db:
            row = db.execute(
                "SELECT data FROM scrim_session_deliveries WHERE session_id=?",
                (session_id,),
            ).fetchone()
        return json.loads(row[0]) if row else {}

    def save_session_delivery(self, session_id, data):
        with self.connection() as db:
            db.execute(
                "INSERT INTO scrim_session_deliveries VALUES (?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET data=excluded.data",
                (session_id, json.dumps(data)),
            )

    def has_snapshot(self, match_id, kind):
        with self.connection() as db:
            return (
                db.execute(
                    "SELECT 1 FROM scrim_snapshots WHERE match_id=? AND kind=? LIMIT 1", (match_id, kind)
                ).fetchone()
                is not None
            )

    def used_ids(self, guild_id):
        with self.connection() as db:
            return {
                row[0]
                for row in db.execute(
                    "SELECT external_id FROM scrim_matches m JOIN scrim_sessions s ON m.session_id=s.id "
                    "WHERE s.guild_id=? AND external_id IS NOT NULL",
                    (guild_id,),
                )
            }

    def end(self, data, actor):
        data["status"] = "ended"
        data.setdefault("ended_at", time.time())
        with self.connection() as db:
            self._save(db, data)
            self._event(db, data["id"], actor, "ended", {})

    def snapshot(self, match_id, kind, payload, external_id=None):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT external_id FROM scrim_matches WHERE id=?", (match_id,)).fetchone()
            if not row:
                raise ValueError("Match not found.")
            if external_id and row[0] and external_id != row[0]:
                raise ValueError("A different game is already linked to this match.")
            if external_id:
                used = db.execute(
                    "SELECT m.id FROM scrim_matches m JOIN scrim_sessions s ON m.session_id=s.id "
                    "WHERE m.external_id=? AND m.id!=? AND s.guild_id=(SELECT s2.guild_id "
                    "FROM scrim_sessions s2 JOIN scrim_matches m2 ON m2.session_id=s2.id WHERE m2.id=?)",
                    (external_id, match_id, match_id),
                ).fetchone()
                if used:
                    raise ValueError("This source game is already logged as another scrim match.")
            db.execute(
                "INSERT INTO scrim_snapshots(match_id, captured_at, kind, data) VALUES (?, ?, ?, ?)",
                (match_id, time.time(), kind, json.dumps(payload)),
            )
            if external_id:
                db.execute("UPDATE scrim_matches SET external_id=? WHERE id=?", (external_id, match_id))
            if kind == "details":
                db.execute("UPDATE scrim_matches SET data=? WHERE id=?", (json.dumps(payload), match_id))
                # Late results refresh the existing session separator in place.
                db.execute(
                    "UPDATE scrim_session_deliveries SET data=json_set(data, '$.done', 0, '$.next_at', 0) "
                    "WHERE session_id=(SELECT session_id FROM scrim_matches WHERE id=?)",
                    (match_id,),
                )

    def export(self, sid):
        session = self.get(sid)
        with self.connection() as db:
            mids = [
                r[0]
                for r in db.execute("SELECT id FROM scrim_matches WHERE session_id=? ORDER BY number", (sid,))
            ]
            events = [
                {"at": r[0], "actor_id": r[1], "action": r[2], "data": json.loads(r[3])}
                for r in db.execute(
                    "SELECT created_at,actor_id,action,data FROM scrim_events WHERE session_id=? ORDER BY id",
                    (sid,),
                )
            ]
            snapshots = [
                {"match_id": r[0], "at": r[1], "kind": r[2], "data": json.loads(r[3])}
                for r in db.execute(
                    "SELECT match_id,captured_at,kind,data FROM scrim_snapshots "
                    "WHERE match_id IN (SELECT id FROM scrim_matches WHERE session_id=?) "
                    "ORDER BY id",
                    (sid,),
                )
            ]
        return {
            "session": session,
            "matches": [self.match(mid) for mid in mids],
            "events": events,
            "snapshots": snapshots,
        }
