"""The ops of ``brd_flow``: read a source, extract, check, hunt for misses, write.

``extract`` is one session: a turn per chunk of sections, then up to
``fix_turns`` turns that send back what failed the checks (a quote not in
the source, a duplicate id, a section that yielded nothing). What still
fails is not written: no unverified quote ever reaches the folder.
``hunt`` is a second session on a different model, asked only for what is
missing; its finds pass the same checks. ``write`` puts the records, the
questions and the matrix rows in the folder.

Each coder turn is checkpointed in a file under the runtime's ``data_dir``
by the op's idempotency key, so a run resumed after a crash does not pay
for the turns that had ended.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from operonx import child, op, run_context
from operonx.core.loggings import LOGGER

from operonx_project.brd.extraction import check_coverage, check_ids, check_quotes
from operonx_project.brd.folder import refresh_summary
from operonx_project.brd.prompts import RECORDS_SCHEMA, chunk_prompt, fix_prompt, hunt_prompt
from operonx_project.brd.readers import Section, normalize, read_source
from operonx_project.brd.records import (
    Question,
    Requirement,
    add_matrix_rows,
    add_questions,
    load_requirements,
    write_requirements,
)
from operonx_project.coder.base import Coder, CoderRequest, CoderResult
from operonx_project.runtime import get_runtime

__all__ = ["BrdRuntime", "extract", "hunt", "read", "write"]


@dataclass
class BrdRuntime:
    """What ``brd_flow``'s ops use; registered with ``runtime.register``."""

    coder: Coder
    data_dir: Path  # outside the repo: a private source's figures never land in git
    models: Dict[str, Optional[str]] = field(default_factory=lambda: {"extractor": "opus", "hunter": "fable"})
    key: str = "brd"
    call_max_usd: float = 10.0
    timeout_s: float = 3600.0
    chunk_chars: int = 12_000
    fix_turns: int = 2
    hunt: bool = True
    languages: str = "vie+eng"

    def __post_init__(self) -> None:
        self.data_dir = Path(self.data_dir)


# ── helpers ────────────────────────────────────────────────────────────────


def _sections(rows: Sequence[Dict]) -> List[Section]:
    return [Section(**r) for r in rows]


def _chunks(sections: Sequence[Section], limit: int) -> List[List[Section]]:
    out: List[List[Section]] = []
    cur: List[Section] = []
    size = 0
    for s in sections:
        n = len(s.text) + len(s.location) + 20
        if cur and size + n > limit:
            out.append(cur)
            cur, size = [], 0
        cur.append(s)
        size += n
    if cur:
        out.append(cur)
    return out


class _Steps:
    """Coder turns saved by an op's idempotency key."""

    def __init__(self, rt: BrdRuntime, op_name: str):
        rc = run_context()
        key = rc.idempotency_key if rc else "nokey"
        self.dir = rt.data_dir / "steps" / f"{op_name}-{key}"

    def get(self, n: str) -> Optional[Dict[str, Any]]:
        f = self.dir / f"{n}.json"
        return json.loads(f.read_text()) if f.exists() else None

    def put(self, n: str, data: Dict[str, Any]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / f"{n}.json").write_text(json.dumps(data, ensure_ascii=False))


async def _turn(rt: BrdRuntime, steps: _Steps, n: str, req: CoderRequest) -> Tuple[Dict[str, Any], CoderResult]:
    saved = steps.get(n)
    if saved is not None:
        return saved, CoderResult(status="ok", session=saved.get("$session"), cost_usd=0.0)
    async with child(n, inputs={"role": req.role, "session": req.session, "chars": len(req.prompt)},
                     op_type="coder") as step:  # fmt: skip
        res = await rt.coder.run(req)
        step.outputs = {"status": res.status, "session": res.session, "error": res.error,
                        "records": len((res.output or {}).get("requirements") or [])}  # fmt: skip
        step.attrs.update({"cost_usd": res.cost_usd, "turns": res.turns, "model": req.model or ""})
    if not res.ok or not isinstance(res.output, dict):
        raise RuntimeError(f"{n}: the session ended {res.status}: {res.error or res.text[-300:]}")
    out = dict(res.output)
    out["$session"] = res.session
    out["$cost"] = res.cost_usd
    steps.put(n, out)
    return out, res


def _req(rt: BrdRuntime, role: str, prompt: str, cwd: Path, session: Optional[str], add: List[Path]) -> CoderRequest:
    return CoderRequest(
        prompt=prompt,
        cwd=cwd,
        role="planner" if role == "extractor" else "reviewer",
        session=session,
        schema=RECORDS_SCHEMA,
        model=rt.models.get(role),
        max_usd=rt.call_max_usd,
        timeout_s=rt.timeout_s,
        transcript=rt.data_dir / "transcripts" / f"{role}.jsonl",
        sandbox=False,  # read-only roles: no shell to sandbox
        add_dirs=add,
        tags={"step": role},
    )


def _records(raw: Sequence[Dict], sid: str) -> List[Tuple[Requirement, Optional[Dict]]]:
    out = []
    for d in raw:
        sec = str(d.get("section", "")).strip().strip("[]").strip()  # "[§20]" as the prompt shows it
        r = Requirement.from_dict({**d, "section": sec, "location": sec, "source": sid})
        r.section = ""
        q = d.get("question")
        r.question = ""
        if isinstance(q, dict) and q.get("topic"):
            r.status = "needs_clarification"
        out.append((r, q if isinstance(q, dict) else None))
    return out


def _descriptive(out: Dict[str, Any]) -> Dict[str, str]:
    return {str(d["section"]).strip().strip("[]").strip(): d.get("reason", "") for d in out.get("descriptive") or []}


def _source_code(rid: str, prefix: str, text_norm: str) -> bool:
    """The id keeps one of the source's own codes (BRD-BR-09 → "BR-09" is in the source)."""
    bare = rid[len(prefix) + 1 :] if rid.startswith(prefix + "-") else rid
    return bool(re.fullmatch(r"(?:[A-Z]+-)?[A-Z]{0,3}\d{2,4}", bare)) and normalize(bare) in text_norm


def _dedupe(recs: List[Requirement], prefix: str, text_norm: str) -> Tuple[List[Requirement], List[str]]:
    """Identical repeats dropped; a new id in a series of ours renumbered; a
    clash on a source code left for the session to fix."""
    seen: Dict[str, Requirement] = {}
    out: List[Requirement] = []
    problems: List[str] = []
    for r in recs:
        if r.id not in seen:
            seen[r.id] = r
            out.append(r)
            continue
        if normalize(seen[r.id].quote) == normalize(r.quote):
            continue
        if _source_code(r.id, prefix, text_norm):
            problems.append(f"{r.id} is used twice for different text: {r.quote[:120]!r}")
            continue
        series = re.sub(r"\d+$", "", r.id)
        width = len(r.id) - len(series)
        n = max(int(i[len(series) :]) for i in seen if i.startswith(series) and i[len(series) :].isdigit()) + 1
        r.id = f"{series}{n:0{width}d}"
        seen[r.id] = r
        out.append(r)
    return out, problems


# ── ops ────────────────────────────────────────────────────────────────────


@op
def read(key: str, brd: str, source: str, sid: str, name: str, only: list) -> dict:
    rt = get_runtime(key)
    src = read_source(Path(source), rt.data_dir / "sources" / sid, rt.languages)
    sections = src.all_sections()
    if only:
        sections = [s for s in sections if s.id in set(only)]
    previous = [r.id for r in load_requirements(Path(brd), [Path(brd) / "requirements" / f"{name}_requirements.md"])]
    return {
        "sections": [s.to_dict() for s in sections],
        "sha256": src.sha256,
        "kind": src.kind,
        "figures": src.figures,
        "previous": previous,
        "skipped": src.skipped,
    }


@op
async def extract(key: str, sid: str, prefix: str, sections: list, previous: list, figures: list) -> dict:
    rt = get_runtime(key)
    secs = _sections(sections)
    text_norm = normalize("\n".join(f"{s.title}\n{s.text}" for s in secs))
    steps = _Steps(rt, "extract")
    cwd = rt.data_dir
    cwd.mkdir(parents=True, exist_ok=True)
    add = sorted({str(Path(f).parent) for f in figures})
    session: Optional[str] = None
    cost = 0.0
    raw: List[Dict] = []
    descriptive: Dict[str, str] = {}
    chunks = _chunks(secs, rt.chunk_chars)
    for n, chunk in enumerate(chunks, 1):
        out, res = await _turn(
            rt, steps, f"chunk-{n:02d}",
            _req(rt, "extractor", chunk_prompt(prefix, chunk, n == 1, len(chunks), n), cwd, session,
                 [Path(a) for a in add]),
        )  # fmt: skip
        session = out.get("$session") or session
        cost += out.get("$cost", 0.0)
        raw += out.get("requirements") or []
        descriptive.update(_descriptive(out))

    pairs = _records(raw, sid)
    recs = [r for r, _ in pairs]
    qs = {id(r): q for r, q in pairs}
    problems_out: List[str] = []
    for turn in range(rt.fix_turns + 1):
        recs, dup = _dedupe(recs, prefix, text_norm)
        bad = check_quotes(recs, secs) + [p for p in check_ids(recs, previous) if p.kind != "dropped_id"]
        uncovered_ids = {p.id for p in check_coverage(secs, recs, descriptive)}
        problems = [p.message + (f": {p.data.get('quote')!r}" if p.data.get("quote") else "") for p in bad] + dup
        if (not problems and not uncovered_ids) or turn == rt.fix_turns or session is None:
            problems_out = problems + [f"section {s} yields no requirement and is not marked descriptive"
                                       for s in sorted(uncovered_ids)]  # fmt: skip
            break
        uncovered = [s for s in secs if s.id in uncovered_ids]
        out, res = await _turn(rt, steps, f"fix-{turn + 1}",
                               _req(rt, "extractor", fix_prompt(problems, uncovered), cwd, session, []))  # fmt: skip
        cost += out.get("$cost", 0.0)
        fixed = _records(out.get("requirements") or [], sid)
        by_id = {r.id: i for i, r in enumerate(recs)}
        for r, q in fixed:
            qs[id(r)] = q
            if r.id in by_id:
                recs[by_id[r.id]] = r
            else:
                recs.append(r)
        descriptive.update(_descriptive(out))

    bad_ids = {p.id for p in check_quotes(recs, secs) if p.kind in ("fake_quote", "bad_type")}
    bad_ids |= {p.id for p in check_ids(recs, previous) if p.kind in ("bad_id", "reused_id")}
    kept = [r for r in recs if r.id not in bad_ids]
    rejected = [r.to_dict() for r in recs if r.id in bad_ids]
    if rejected:
        LOGGER.warning(f"extract {sid}: {len(rejected)} record(s) still fail the checks; not written")
    return {
        "records": [{**r.to_dict(), "$question": qs.get(id(r))} for r in kept],
        "descriptive": descriptive,
        "rejected": rejected,
        "problems": problems_out,
        "session": session,
        "cost_usd": cost,
    }


@op
async def hunt(key: str, sid: str, prefix: str, sections: list, records: list, figures: list) -> dict:
    rt = get_runtime(key)
    if not rt.hunt:
        return {"records": records, "found": [], "rejected": [], "cost_usd": 0.0}
    secs = _sections(sections)
    have = [{"id": r["id"], "section": r["section"], "reading": (r.get("reading") or r["quote"])[:160]}
            for r in records]  # fmt: skip
    steps = _Steps(rt, "hunt")
    add = sorted({str(Path(f).parent) for f in figures})
    out, _ = await _turn(rt, steps, "hunt",
                         _req(rt, "hunter", hunt_prompt(prefix, secs, have), rt.data_dir, None,
                              [Path(a) for a in add]))  # fmt: skip
    found = _records(out.get("requirements") or [], sid)
    known = {normalize(r["quote"]) for r in records}
    ids = {r["id"] for r in records}
    new: List[Requirement] = []
    qs: Dict[int, Any] = {}
    for r, q in found:
        if normalize(r.quote) in known:
            continue  # it was there already
        qs[id(r)] = q
        new.append(r)
    text_norm = normalize("\n".join(f"{s.title}\n{s.text}" for s in secs))
    prior = [Requirement.from_dict(r) for r in records]
    merged, _ = _dedupe(prior + new, prefix, text_norm)
    new = [r for r in merged[len(prior) :]]
    bad = {p.id for p in check_quotes(new, secs) if p.kind in ("fake_quote", "bad_type")}
    bad |= {p.id for p in check_ids(new) if p.kind == "bad_id"} | (ids & {r.id for r in new})
    good = [r for r in new if r.id not in bad]
    return {
        "records": records + [{**r.to_dict(), "$question": qs.get(id(r))} for r in good],
        "found": [r.id for r in good],
        "rejected": [r.to_dict() for r in new if r.id in bad],
        "cost_usd": out.get("$cost", 0.0),
    }


@op
def write(key: str, brd: str, name: str, title: str, sid: str, records: list, descriptive: dict,
          sections: list, problems: list, rejected: list, found: list, hunt_rejected: list,
          extract_cost: float, hunt_cost: float) -> dict:  # fmt: skip
    brd_dir = Path(brd)
    secs = {s["id"]: s for s in sections}
    reqs: List[Requirement] = []
    questions: List[Tuple[Requirement, Question]] = []
    for d in records:
        q = d.get("$question")
        r = Requirement.from_dict(d)
        if r.section in secs:
            r.location = secs[r.section]["location"]
        reqs.append(r)
        if isinstance(q, dict) and q.get("topic"):
            qt = q.get("type") if q.get("type") in ("ambiguity", "conflict", "divergence", "gap") else "ambiguity"
            questions.append((r, Question("Q-00", qt, q["topic"], q.get("fact", ""), q.get("interpretation", ""),
                                          q.get("who") or "the owner", requirements=[r.id])))  # fmt: skip
    added = add_questions(brd_dir, [q for _, q in questions])
    by_topic = {" ".join(q.topic.lower().split()): q.id for q in added}
    for r, q in questions:
        r.question = by_topic.get(" ".join(q.topic.lower().split()), "")
    f = write_requirements(brd_dir, name, title, f"{sid} ({title})", reqs)
    rows = add_matrix_rows(brd_dir, reqs, title)
    refresh_summary(brd_dir)
    digest = hashlib.sha256(f.read_bytes()).hexdigest()[:12]
    return {
        "file": str(f),
        "requirements": len(reqs),
        "matrix_rows": len(rows),
        "questions": [q.id for q in added],
        "descriptive": descriptive,
        "digest": digest,
        "problems": problems,  # what still failed after the fix turns
        "rejected": rejected + hunt_rejected,  # records not written: their quote is not in the source
        "found_by_hunter": found,
        "cost_usd": round(extract_cost + hunt_cost, 4),
    }
