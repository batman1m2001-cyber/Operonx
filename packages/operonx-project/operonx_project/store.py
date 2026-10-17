"""The project store: milestones, cards and the event log, in one SQLite file.

It lives in the project (``.operonx/project/project.db``, git-ignored). Every
state change goes through :meth:`Store.move_card` or
:meth:`Store.move_milestone`: the move is checked against a transition table,
written with its event row in one transaction, and an illegal move raises
:class:`IllegalTransition`. The event log is the audit trail (who, what, when,
why) and what the studio's board follows by cursor.

The schema is versioned (``meta.schema_version``) because the studio reads it
directly, read-only.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import fields
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Union

from operonx_project.model import Card, CardState, Milestone, MilestoneState, Policy, Stage

__all__ = ["CARD_MOVES", "IllegalTransition", "MILESTONE_MOVES", "SCHEMA_VERSION", "Store"]

SCHEMA_VERSION = 2  # 2: commands, interactions, updates

C = CardState
#: Legal card moves. A running card's stage (implement → review → qa) is not
#: a move: :meth:`Store.set_stage` changes it.
CARD_MOVES: Dict[CardState, frozenset] = {
    C.PENDING: frozenset({C.READY, C.BLOCKED, C.CANCELLED}),
    C.READY: frozenset({C.RUNNING, C.PENDING, C.BLOCKED, C.CANCELLED}),
    # running → ready: requeued (a rate limit, a drain) without a verdict
    C.RUNNING: frozenset({C.WAITING_HUMAN, C.VERIFIED, C.FAILED, C.CANCELLED, C.READY}),
    C.WAITING_HUMAN: frozenset({C.RUNNING, C.READY, C.CANCELLED, C.FAILED}),
    # verified → ready: an integration conflict or regression re-opens it
    C.VERIFIED: frozenset({C.DONE, C.READY, C.FAILED, C.CANCELLED}),
    # done → ready: a final-verification gap or a changed source re-opens it
    C.DONE: frozenset({C.READY}),
    C.FAILED: frozenset({C.READY, C.CANCELLED}),  # the owner retries it; a replan retires it
    C.BLOCKED: frozenset({C.PENDING, C.CANCELLED}),
    C.CANCELLED: frozenset(),
}

M = MilestoneState
_STAGES = {M.DISCOVERING, M.REQUIREMENTS, M.PLANNING, M.RUNNING, M.VERIFYING}
MILESTONE_MOVES: Dict[MilestoneState, frozenset] = {
    M.CREATED: frozenset({M.DISCOVERING, M.REQUIREMENTS, M.PLANNING, M.CANCELLED}),
    M.DISCOVERING: frozenset({M.REQUIREMENTS, M.PLANNING, M.WAITING_HUMAN, M.PAUSED, M.FAILED, M.CANCELLED}),
    M.REQUIREMENTS: frozenset({M.PLANNING, M.WAITING_HUMAN, M.PAUSED, M.FAILED, M.CANCELLED}),
    M.PLANNING: frozenset({M.AWAITING_APPROVAL, M.RUNNING, M.WAITING_HUMAN, M.PAUSED, M.FAILED, M.CANCELLED}),
    M.AWAITING_APPROVAL: frozenset({M.RUNNING, M.PLANNING, M.PAUSED, M.FAILED, M.CANCELLED}),
    M.RUNNING: frozenset(
        {M.WAITING_HUMAN, M.PAUSED, M.VERIFYING, M.PLANNING, M.FAILED, M.CANCELLED, M.BUDGET_EXHAUSTED}
    ),
    M.WAITING_HUMAN: frozenset(_STAGES | {M.PAUSED, M.FAILED, M.CANCELLED, M.BUDGET_EXHAUSTED}),
    M.PAUSED: frozenset(_STAGES | {M.WAITING_HUMAN, M.CANCELLED}),
    M.VERIFYING: frozenset({M.DONE, M.RUNNING, M.WAITING_HUMAN, M.PAUSED, M.FAILED, M.CANCELLED}),
    M.BUDGET_EXHAUSTED: frozenset({M.RUNNING, M.PAUSED, M.FAILED, M.CANCELLED}),
    M.FAILED: frozenset({M.PLANNING, M.RUNNING}),  # the owner retries
    M.DONE: frozenset({M.RUNNING}),  # a changed source re-opens it
    M.CANCELLED: frozenset(),
}


class IllegalTransition(ValueError):
    """A move the transition table does not allow."""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS milestones (
    id TEXT PRIMARY KEY, project TEXT, title TEXT, goal TEXT, rows TEXT, state TEXT,
    prior TEXT, policy TEXT, reason TEXT, cost_usd REAL, replans INTEGER, resume_at REAL,
    created REAL, updated REAL);
CREATE TABLE IF NOT EXISTS cards (
    id TEXT, milestone TEXT, title TEXT, intent TEXT, rows TEXT, criteria TEXT, deps TEXT,
    paths TEXT, kind TEXT, risk TEXT, priority INTEGER, epic TEXT, state TEXT, stage TEXT,
    reason TEXT, reopens INTEGER, run_id TEXT, cost_usd REAL, detail TEXT,
    created REAL, updated REAL, PRIMARY KEY (milestone, id));
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, at REAL, milestone TEXT, card TEXT,
    kind TEXT, actor TEXT, data TEXT);
CREATE INDEX IF NOT EXISTS events_by_milestone ON events (milestone, seq);
CREATE TABLE IF NOT EXISTS lease (name TEXT PRIMARY KEY, owner TEXT, until REAL);
CREATE TABLE IF NOT EXISTS steps (key TEXT PRIMARY KEY, at REAL, data TEXT);
CREATE TABLE IF NOT EXISTS commands (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, at REAL, milestone TEXT, card TEXT, kind TEXT,
    payload TEXT, actor TEXT, status TEXT, result TEXT, done_at REAL);
CREATE TABLE IF NOT EXISTS interactions (
    id TEXT PRIMARY KEY, short TEXT UNIQUE, milestone TEXT, card TEXT, kind TEXT, qid TEXT,
    version INTEGER, context_hash TEXT, payload TEXT, state TEXT, answer TEXT, answered_by TEXT,
    answered_at REAL, command INTEGER, delivery TEXT, messages TEXT, shown TEXT, created REAL,
    reminded_at REAL, expires_at REAL, flagged INTEGER, updated REAL);
CREATE TABLE IF NOT EXISTS updates (update_id INTEGER PRIMARY KEY, at REAL);
"""

_JSON_CARD = ("rows", "criteria", "deps", "paths", "detail")
_CARD_COLS = [f.name for f in fields(Card)]
_MS_COLS = [f.name for f in fields(Milestone)]


class Store:
    """One project's store. Safe to share between threads of one process; any
    number of processes may open the same file (WAL), with one writer at a time."""

    def __init__(self, path: Union[str, Path]):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(self.path), check_same_thread=False, timeout=30, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA busy_timeout=30000")
            self._db.executescript(_SCHEMA)
            got = self._db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if got is None:
                self._db.execute("INSERT INTO meta VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
            elif int(got[0]) > SCHEMA_VERSION:
                raise RuntimeError(f"{self.path} has schema {got[0]}; this operonx-project reads {SCHEMA_VERSION}")
            elif int(got[0]) < SCHEMA_VERSION:  # the new tables are made above; nothing else changed
                self._db.execute("UPDATE meta SET value=? WHERE key='schema_version'", (str(SCHEMA_VERSION),))

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ── transactions ────────────────────────────────────────────────────────
    def _tx(self):
        store = self

        class _Tx:
            def __enter__(self):
                store._lock.acquire()
                store._db.execute("BEGIN IMMEDIATE")
                return store._db

            def __exit__(self, kind, exc, tb):
                try:
                    store._db.execute("ROLLBACK" if kind else "COMMIT")
                finally:
                    store._lock.release()
                return False

        return _Tx()

    def _event(self, db, milestone: str, card: Optional[str], kind: str, actor: str, data: Dict[str, Any]) -> int:
        cur = db.execute(
            "INSERT INTO events (at, milestone, card, kind, actor, data) VALUES (?, ?, ?, ?, ?, ?)",
            (time.time(), milestone, card, kind, actor, json.dumps(data, default=str)),
        )
        return int(cur.lastrowid)

    # ── milestones ──────────────────────────────────────────────────────────
    def add_milestone(self, ms: Milestone, actor: str = "owner") -> Milestone:
        row = self._ms_row(ms)
        with self._tx() as db:
            db.execute(
                f"INSERT INTO milestones ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", list(row.values())
            )
            self._event(db, ms.id, None, "milestone.created", actor, {"title": ms.title, "state": ms.state.value})
        return ms

    def get_milestone(self, mid: str) -> Milestone:
        with self._lock:
            row = self._db.execute("SELECT * FROM milestones WHERE id=?", (mid,)).fetchone()
        if row is None:
            raise KeyError(f"no milestone {mid!r}")
        return self._ms_obj(row)

    def milestones(self) -> List[Milestone]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM milestones ORDER BY created").fetchall()
        return [self._ms_obj(r) for r in rows]

    def prior_state(self, mid: str) -> Optional[MilestoneState]:
        """The stage a paused or waiting milestone left (what a resume returns to)."""
        with self._lock:
            row = self._db.execute("SELECT prior FROM milestones WHERE id=?", (mid,)).fetchone()
        return MilestoneState(row[0]) if row and row[0] else None

    def move_milestone(self, mid: str, to: MilestoneState, reason: str = "", actor: str = "engine") -> Milestone:
        to = MilestoneState(to)
        with self._tx() as db:
            row = db.execute("SELECT state FROM milestones WHERE id=?", (mid,)).fetchone()
            if row is None:
                raise KeyError(f"no milestone {mid!r}")
            frm = MilestoneState(row[0])
            if to not in MILESTONE_MOVES[frm]:
                raise IllegalTransition(f"milestone {mid}: {frm.value} → {to.value} is not allowed")
            prior = frm.value if to in (M.PAUSED, M.WAITING_HUMAN) and frm in _STAGES else None
            db.execute(
                "UPDATE milestones SET state=?, reason=?, updated=?, prior=COALESCE(?, prior) WHERE id=?",
                (to.value, reason, time.time(), prior, mid),
            )
            self._event(db, mid, None, "milestone.state", actor, {"from": frm.value, "to": to.value, "reason": reason})
        return self.get_milestone(mid)

    def update_milestone(self, mid: str, actor: str = "engine", **values: Any) -> Milestone:
        if "state" in values:
            raise ValueError("change a milestone's state with move_milestone")
        bad = set(values) - set(_MS_COLS)
        if bad:
            raise ValueError(f"unknown milestone fields: {sorted(bad)}")
        enc = {k: self._enc_ms(k, v) for k, v in values.items()}
        with self._tx() as db:
            db.execute(
                f"UPDATE milestones SET {', '.join(f'{k}=?' for k in enc)}, updated=? WHERE id=?",
                [*enc.values(), time.time(), mid],
            )
            self._event(db, mid, None, "milestone.update", actor, {k: v for k, v in values.items() if k != "policy"})
        return self.get_milestone(mid)

    def add_cost(self, mid: str, usd: float, card: Optional[str] = None) -> None:
        with self._tx() as db:
            db.execute("UPDATE milestones SET cost_usd = cost_usd + ? WHERE id=?", (usd, mid))
            if card is not None:
                db.execute("UPDATE cards SET cost_usd = cost_usd + ? WHERE milestone=? AND id=?", (usd, mid, card))

    # ── cards ───────────────────────────────────────────────────────────────
    def add_cards(self, cards: Iterable[Card], actor: str = "planner") -> List[Card]:
        cards = list(cards)
        with self._tx() as db:
            for card in cards:
                row = self._card_row(card)
                db.execute(
                    f"INSERT INTO cards ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", list(row.values())
                )
                self._event(
                    db,
                    card.milestone,
                    card.id,
                    "card.created",
                    actor,
                    {"title": card.title, "deps": card.deps, "state": card.state.value},
                )
        return cards

    def get_card(self, mid: str, cid: str) -> Card:
        with self._lock:
            row = self._db.execute("SELECT * FROM cards WHERE milestone=? AND id=?", (mid, cid)).fetchone()
        if row is None:
            raise KeyError(f"no card {cid!r} in milestone {mid!r}")
        return self._card_obj(row)

    def cards(self, mid: str) -> List[Card]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM cards WHERE milestone=? ORDER BY priority, created, id", (mid,)
            ).fetchall()
        return [self._card_obj(r) for r in rows]

    def move_card(
        self, mid: str, cid: str, to: CardState, reason: str = "", actor: str = "engine", **values: Any
    ) -> Card:
        """Move a card, checked; *values* (run_id, reopens, detail, ...) are set in the same write."""
        to = CardState(to)
        bad = set(values) - set(_CARD_COLS) | ({"state", "stage"} & set(values))
        if bad:
            raise ValueError(f"cannot set {sorted(bad)} in a move")
        with self._tx() as db:
            row = db.execute("SELECT state FROM cards WHERE milestone=? AND id=?", (mid, cid)).fetchone()
            if row is None:
                raise KeyError(f"no card {cid!r} in milestone {mid!r}")
            frm = CardState(row[0])
            if to not in CARD_MOVES[frm]:
                raise IllegalTransition(f"card {cid}: {frm.value} → {to.value} is not allowed")
            stage = Stage.IMPLEMENT.value if to == C.RUNNING else None
            enc = {k: self._enc_card(k, v) for k, v in values.items()}
            sets = ", ".join(["state=?", "stage=?", "reason=?", "updated=?", *(f"{k}=?" for k in enc)])
            db.execute(
                f"UPDATE cards SET {sets} WHERE milestone=? AND id=?",
                [to.value, stage, reason, time.time(), *enc.values(), mid, cid],
            )
            self._event(db, mid, cid, "card.state", actor, {"from": frm.value, "to": to.value, "reason": reason})
        return self.get_card(mid, cid)

    def set_stage(self, mid: str, cid: str, stage: Stage, actor: str = "engine") -> None:
        stage = Stage(stage)
        with self._tx() as db:
            row = db.execute("SELECT state FROM cards WHERE milestone=? AND id=?", (mid, cid)).fetchone()
            if row is None or CardState(row[0]) != C.RUNNING:
                raise IllegalTransition(f"card {cid}: only a running card has a stage")
            db.execute(
                "UPDATE cards SET stage=?, updated=? WHERE milestone=? AND id=?", (stage.value, time.time(), mid, cid)
            )
            self._event(db, mid, cid, "card.stage", actor, {"stage": stage.value})

    def update_card(self, mid: str, cid: str, actor: str = "engine", **values: Any) -> Card:
        if {"state", "stage"} & set(values):
            raise ValueError("change a card's state with move_card, its stage with set_stage")
        bad = set(values) - set(_CARD_COLS)
        if bad:
            raise ValueError(f"unknown card fields: {sorted(bad)}")
        enc = {k: self._enc_card(k, v) for k, v in values.items()}
        with self._tx() as db:
            db.execute(
                f"UPDATE cards SET {', '.join(f'{k}=?' for k in enc)}, updated=? WHERE milestone=? AND id=?",
                [*enc.values(), time.time(), mid, cid],
            )
            self._event(db, mid, cid, "card.update", actor, {k: v for k, v in values.items() if k != "detail"})
        return self.get_card(mid, cid)

    def note(self, mid: str, kind: str, data: Dict[str, Any], card: Optional[str] = None, actor: str = "engine") -> int:
        """An event that is not a state change: a round's result, a cost, a message sent."""
        with self._tx() as db:
            return self._event(db, mid, card, kind, actor, data)

    def events(self, mid: str, after: int = 0, limit: int = 1000) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM events WHERE milestone=? AND seq>? ORDER BY seq LIMIT ?", (mid, after, limit)
            ).fetchall()
        return [{**dict(r), "data": json.loads(r["data"])} for r in rows]

    # ── step checkpoints ─────────────────────────────────────────────────────
    def checkpoint(self, key: str) -> Optional[Dict[str, Any]]:
        """What a step saved under *key* (a run's idempotency key and the
        step's name), or ``None``: a round re-run after a crash skips the
        steps that had ended, such as a finished coder session."""
        with self._lock:
            row = self._db.execute("SELECT data FROM steps WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def save_checkpoint(self, key: str, data: Dict[str, Any]) -> None:
        with self._tx() as db:
            db.execute("INSERT OR REPLACE INTO steps VALUES (?, ?, ?)", (key, time.time(), json.dumps(data)))

    # ── a milestone's records: its brief, baseline, plan, stage runs ─────────
    def ms_get(self, mid: str, key: str) -> Optional[Dict[str, Any]]:
        return self.checkpoint(f"ms:{mid}:{key}")

    def ms_put(self, mid: str, key: str, data: Dict[str, Any]) -> None:
        self.save_checkpoint(f"ms:{mid}:{key}", data)

    # ── the command queue: the CLI, the studio and Telegram write; the daemon runs ──
    COMMANDS = ("start", "approve", "answer", "pause", "unpause", "cancel", "replan", "retry")

    def enqueue(self, mid: str, kind: str, payload: Optional[Dict[str, Any]] = None, card: Optional[str] = None,
                actor: str = "owner") -> int:  # fmt: skip
        """Queue a command; it survives a crash until the daemon runs it."""
        if kind not in self.COMMANDS:
            raise ValueError(f"unknown command {kind!r}; one of {self.COMMANDS}")
        with self._tx() as db:
            cur = db.execute(
                "INSERT INTO commands (at, milestone, card, kind, payload, actor, status) VALUES (?,?,?,?,?,?,?)",
                (time.time(), mid, card, kind, json.dumps(payload or {}), actor, "pending"),
            )
            self._event(db, mid, card, "command", actor, {"kind": kind, "seq": cur.lastrowid})
            return int(cur.lastrowid)

    def pending_commands(self, mid: Optional[str] = None) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM commands WHERE status='pending'" + (" AND milestone=?" if mid else "") + " ORDER BY seq"
        with self._lock:
            rows = self._db.execute(sql, (mid,) if mid else ()).fetchall()
        return [{**dict(r), "payload": json.loads(r["payload"] or "{}")} for r in rows]

    def finish_command(self, seq: int, status: str = "done", result: str = "") -> None:
        with self._tx() as db:
            db.execute("UPDATE commands SET status=?, result=?, done_at=? WHERE seq=?",
                       (status, result, time.time(), seq))  # fmt: skip

    def command(self, seq: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._db.execute("SELECT * FROM commands WHERE seq=?", (seq,)).fetchone()
        return None if row is None else {**dict(row), "payload": json.loads(row["payload"] or "{}")}

    # ── interactions: the questions the owner is asked (plan §9) ─────────────
    _JSON_INTER = ("payload", "answer", "messages", "shown")

    def _inter(self, row: sqlite3.Row) -> Dict[str, Any]:
        d = dict(row)
        for k in self._JSON_INTER:
            d[k] = json.loads(d[k]) if d[k] else ({} if k in ("payload", "shown") else None if k == "answer" else [])
        return d

    def add_interaction(self, inter: Dict[str, Any], actor: str = "engine") -> Dict[str, Any]:
        row = {k: (json.dumps(v) if k in self._JSON_INTER else v) for k, v in inter.items()}
        with self._tx() as db:
            db.execute(f"INSERT INTO interactions ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                       list(row.values()))  # fmt: skip
            self._event(db, inter["milestone"], inter.get("card"), "interaction.open", actor,
                        {"id": inter["id"], "qid": inter.get("qid"), "kind": inter.get("kind")})  # fmt: skip
        return self.get_interaction(inter["id"])

    def get_interaction(self, iid: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._db.execute("SELECT * FROM interactions WHERE id=? OR short=?", (iid, iid)).fetchone()
        return None if row is None else self._inter(row)

    def interactions(self, mid: Optional[str] = None, states: Optional[Iterable[str]] = None) -> List[Dict[str, Any]]:
        sql, args = "SELECT * FROM interactions WHERE 1=1", []
        if mid:
            sql, args = sql + " AND milestone=?", args + [mid]
        states = list(states or [])
        if states:
            sql, args = sql + f" AND state IN ({', '.join('?' * len(states))})", args + states
        with self._lock:
            rows = self._db.execute(sql + " ORDER BY created", args).fetchall()
        return [self._inter(r) for r in rows]

    def update_interaction(self, iid: str, actor: str = "engine", **values: Any) -> Dict[str, Any]:
        enc = {k: (json.dumps(v) if k in self._JSON_INTER else v) for k, v in values.items()}
        with self._tx() as db:
            row = db.execute("SELECT milestone, card, state FROM interactions WHERE id=?", (iid,)).fetchone()
            db.execute(f"UPDATE interactions SET {', '.join(f'{k}=?' for k in enc)}, updated=? WHERE id=?",
                       [*enc.values(), time.time(), iid])  # fmt: skip
            if "state" in values and row is not None and values["state"] != row["state"]:
                self._event(db, row["milestone"], row["card"], "interaction.state", actor,
                            {"id": iid, "from": row["state"], "to": values["state"]})  # fmt: skip
        return self.get_interaction(iid)

    def answer_interaction(self, iid: str, answer: Any, actor: str, kind: str, payload: Dict[str, Any],
                           card: Optional[str] = None) -> Optional[int]:  # fmt: skip
        """Record the answer and queue its command in one transaction: the
        answer is in the store before anything acts on it (plan §9.5).
        ``None`` when the interaction was no longer open."""
        now = time.time()
        with self._tx() as db:
            row = db.execute("SELECT milestone, state FROM interactions WHERE id=?", (iid,)).fetchone()
            if row is None or row["state"] != "open":
                return None
            mid = row["milestone"]
            cur = db.execute(
                "INSERT INTO commands (at, milestone, card, kind, payload, actor, status) VALUES (?,?,?,?,?,?,?)",
                (now, mid, card, kind, json.dumps({**payload, "interaction": iid}), actor, "pending"),
            )
            db.execute("UPDATE interactions SET state='answered', answer=?, answered_by=?, answered_at=?, command=?, "
                       "updated=? WHERE id=?", (json.dumps(answer), actor, now, cur.lastrowid, now, iid))  # fmt: skip
            self._event(db, mid, card, "interaction.answered", actor, {"id": iid, "command": cur.lastrowid})
            return int(cur.lastrowid)

    def find_interaction_by_message(self, chat: Any, message_id: int) -> Optional[Dict[str, Any]]:
        for inter in self.interactions():
            if any(str(m.get("chat")) == str(chat) and m.get("id") == message_id for m in inter["messages"]):
                return inter
        return None

    def first_update(self, update_id: int) -> bool:
        """``True`` the first time an update id is seen: each is handled once."""
        with self._tx() as db:
            if db.execute("SELECT 1 FROM updates WHERE update_id=?", (update_id,)).fetchone():
                return False
            db.execute("INSERT INTO updates VALUES (?, ?)", (update_id, time.time()))
            return True

    # ── the daemon's lease: one daemon drives a project ─────────────────────
    def acquire_lease(self, owner: str, ttl: float = 30.0, name: str = "daemon") -> bool:
        now = time.time()
        with self._tx() as db:
            row = db.execute("SELECT owner, until FROM lease WHERE name=?", (name,)).fetchone()
            if row is not None and row["owner"] != owner and row["until"] > now:
                return False
            db.execute("INSERT OR REPLACE INTO lease VALUES (?, ?, ?)", (name, owner, now + ttl))
            return True

    def release_lease(self, owner: str, name: str = "daemon") -> None:
        with self._tx() as db:
            db.execute("DELETE FROM lease WHERE name=? AND owner=?", (name, owner))

    # ── encoding ────────────────────────────────────────────────────────────
    @staticmethod
    def _enc_card(key: str, value: Any) -> Any:
        if key in _JSON_CARD:
            return json.dumps(value)
        if isinstance(value, (CardState, Stage)):
            return value.value
        return value

    def _card_row(self, card: Card) -> Dict[str, Any]:
        return {k: self._enc_card(k, getattr(card, k)) for k in _CARD_COLS}

    @staticmethod
    def _card_obj(row: sqlite3.Row) -> Card:
        data = {k: row[k] for k in _CARD_COLS}
        for k in _JSON_CARD:
            data[k] = json.loads(data[k]) if data[k] else ([] if k != "detail" else {})
        return Card(**data)

    @staticmethod
    def _enc_ms(key: str, value: Any) -> Any:
        if key == "rows":
            return json.dumps(value)
        if key == "policy":
            return json.dumps(value.to_dict() if isinstance(value, Policy) else value)
        if isinstance(value, MilestoneState):
            return value.value
        return value

    def _ms_row(self, ms: Milestone) -> Dict[str, Any]:
        return {k: self._enc_ms(k, getattr(ms, k)) for k in _MS_COLS}

    @staticmethod
    def _ms_obj(row: sqlite3.Row) -> Milestone:
        data = {k: row[k] for k in _MS_COLS}
        data["rows"] = json.loads(data["rows"]) if data["rows"] else []
        data["policy"] = Policy.from_dict(json.loads(data["policy"]) if data["policy"] else None)
        return Milestone(**data)
