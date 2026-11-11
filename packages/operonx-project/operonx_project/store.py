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
    M.CREATED: frozenset({M.DISCOVERING, M.REQUIREMENTS, M.PLANNING, M.CANCELLED, M.WAITING_DEPENDENCY}),
    M.WAITING_DEPENDENCY: frozenset({M.DISCOVERING, M.CANCELLED}),
    M.DISCOVERING: frozenset(
        {M.REQUIREMENTS, M.PLANNING, M.WAITING_HUMAN, M.PAUSED, M.FAILED, M.CANCELLED, M.BUDGET_EXHAUSTED}
    ),
    M.REQUIREMENTS: frozenset({M.PLANNING, M.WAITING_HUMAN, M.PAUSED, M.FAILED, M.CANCELLED, M.BUDGET_EXHAUSTED}),
    M.PLANNING: frozenset(
        {M.AWAITING_APPROVAL, M.RUNNING, M.WAITING_HUMAN, M.PAUSED, M.FAILED, M.CANCELLED, M.BUDGET_EXHAUSTED}
    ),
    M.AWAITING_APPROVAL: frozenset({M.RUNNING, M.PLANNING, M.PAUSED, M.FAILED, M.CANCELLED}),
    M.RUNNING: frozenset(
        {M.WAITING_HUMAN, M.PAUSED, M.VERIFYING, M.PLANNING, M.FAILED, M.CANCELLED, M.BUDGET_EXHAUSTED}
    ),
    M.WAITING_HUMAN: frozenset(_STAGES | {M.PAUSED, M.FAILED, M.CANCELLED, M.BUDGET_EXHAUSTED}),
    M.PAUSED: frozenset(_STAGES | {M.WAITING_HUMAN, M.CANCELLED}),
    M.VERIFYING: frozenset({M.DONE, M.RUNNING, M.WAITING_HUMAN, M.PAUSED, M.FAILED, M.CANCELLED, M.BUDGET_EXHAUSTED}),
    M.BUDGET_EXHAUSTED: frozenset(_STAGES | {M.PAUSED, M.FAILED, M.CANCELLED}),  # retry resumes the stage it left
    M.FAILED: frozenset({M.DISCOVERING, M.PLANNING, M.RUNNING}),  # the owner retries or replans
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

    def running_seconds(self, mid: str, now: Optional[float] = None) -> float:
        """Wall-clock seconds the milestone has spent in a working stage (``max_hours``):
        read from its state events, so a restart does not reset it; waiting for the
        owner, a plan to approve, a pause do not count."""
        with self._lock:
            rows = self._db.execute(
                "SELECT at, data FROM events WHERE milestone=? AND kind='milestone.state' ORDER BY seq", (mid,)
            ).fetchall()
        total, since = 0.0, None
        for at, data in rows:
            to = MilestoneState(json.loads(data)["to"])
            if to in _STAGES and since is None:
                since = at
            elif to not in _STAGES and since is not None:
                total, since = total + (at - since), None
        if since is not None:
            total += (time.time() if now is None else now) - since
        return total

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

    def mirror(self, ms: Milestone, cards: Iterable[Card], actor: str = "plan") -> None:
        """Make *ms* and its cards exactly these, unchecked by the transition table: a milestone whose
        record is a plan doc worked outside the engine (api ``plan``), where the doc says each state."""
        cards = list(cards)
        row = self._ms_row(ms)
        with self._tx() as db:
            db.execute(
                f"INSERT OR REPLACE INTO milestones ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                list(row.values()),
            )
            db.execute("DELETE FROM cards WHERE milestone=?", (ms.id,))
            for card in cards:
                crow = self._card_row(card)
                db.execute(f"INSERT INTO cards ({', '.join(crow)}) VALUES ({', '.join('?' * len(crow))})",
                           list(crow.values()))  # fmt: skip
            self._event(db, ms.id, None, "plan.sync", actor,
                        {"state": ms.state.value, "cards": {c.id: c.state.value for c in cards}})  # fmt: skip

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
    COMMANDS = (
        "start",
        "approve",
        "answer",
        "pause",
        "unpause",
        "cancel",
        "replan",
        "retry",
        "take",
        "submit",
        "release",
    )

    def commands(self, mid: str) -> List[Dict[str, Any]]:
        """Every command queued for *mid*, oldest first."""
        with self._lock:
            rows = self._db.execute("SELECT * FROM commands WHERE milestone=? ORDER BY seq", (mid,)).fetchall()
        return [{**dict(r), "payload": json.loads(r["payload"] or "{}")} for r in rows]

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
