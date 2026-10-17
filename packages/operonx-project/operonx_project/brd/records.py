"""Requirement records, open questions and the traceability matrix, as Markdown.

The folder is the record (plan §4). Requirements are table rows in
``requirements/<source>_requirements.md`` that start with their ID, so
OneWealth's checker and ours both find them. The matrix,
``requirements/requirements_traceability.md``, holds one row per
requirement and is the only place a status lives. Questions are ``### Q-nn``
entries in ``requirements/open_questions.md``, with an index table.

Writers never drop a row: an ID that is retired is marked ``superseded``,
and an existing matrix row keeps its status, its tests and its evidence.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

__all__ = [
    "MATRIX",
    "QUESTION_TYPES",
    "REQ_TYPES",
    "STATUSES",
    "Question",
    "Requirement",
    "add_matrix_rows",
    "add_questions",
    "load_questions",
    "load_requirements",
    "matrix_status",
    "resolve_question",
    "set_matrix_row",
    "write_requirements",
]

MATRIX = "requirements/requirements_traceability.md"
QUESTIONS = "requirements/open_questions.md"
REQ_TYPES = ("business", "functional", "non_functional", "rule", "kpi")
QUESTION_TYPES = ("ambiguity", "conflict", "divergence", "gap")
STATUSES = (
    "implemented_verified",
    "implemented_unverified",
    "partially_implemented",
    "planned",
    "not_implemented",
    "blocked",
    "out_of_scope_demo",
    "not_applicable",
)
_TYPE_TITLES = {
    "business": "Business requirements",
    "functional": "Functional requirements",
    "non_functional": "Non-functional requirements",
    "rule": "Business rules",
    "kpi": "Success metrics",
}


def _cell(text: str) -> str:
    return str(text or "").replace("|", "\\|").replace("\n", " ").strip()


def _uncell(text: str) -> str:
    return text.replace("\\|", "|").strip()


def _split(line: str) -> List[str]:
    return [_uncell(c) for c in re.split(r"(?<!\\)\|", line.strip().strip("|"))]


@dataclass
class Requirement:
    id: str
    type: str  # business | functional | non_functional | rule | kpi
    quote: str  # in the source's language
    location: str  # "§6.1", "p. 3", "image", as the reader named the section
    reading: str = ""  # what it means, in the folder's language
    source: str = ""  # SRC-nn
    section: str = ""  # the section id the engine found the quote in
    paraphrase: bool = False  # allowed only when marked
    status: str = "documented"  # documented | needs_clarification | superseded
    question: str = ""  # Q-nn when needs_clarification
    superseded_by: str = ""

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict) -> "Requirement":
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d}
        return cls(**known)

    def row(self) -> str:
        quote = ("(paraphrase) " if self.paraphrase else "") + self.quote
        doc = self.status + (f" ({self.question})" if self.question else "")
        if self.superseded_by:
            doc += f" by {self.superseded_by}"
        return f"| {self.id} | {_cell(self.location)} | {_cell(quote)} | {_cell(self.reading)} | {_cell(doc)} |"


_REQ_HEADER = "| ID | Location | Quote (source language) | Reading | Doc status |\n|---|---|---|---|---|"


def write_requirements(brd: Path, name: str, title: str, source: str, reqs: Sequence[Requirement]) -> Path:
    """Write (or rewrite) one source's requirements file, grouped by type.
    IDs already in the file and absent now are kept, marked superseded."""
    f = Path(brd) / "requirements" / f"{name}_requirements.md"
    f.parent.mkdir(parents=True, exist_ok=True)
    reqs = list(reqs)
    have = {r.id for r in reqs}
    for old in load_requirements(brd, [f]):
        if old.id not in have:
            old.status = "superseded"
            reqs.append(old)
    lines = [
        f"# {title}",
        "",
        f"Extracted from {source}. The quote is the source's own words, at the location given; the reading is "
        "ours. Status is not here: see [`requirements_traceability.md`](requirements_traceability.md).",
        "",
    ]
    for t in REQ_TYPES:
        group = [r for r in reqs if r.type == t]
        if not group:
            continue
        lines += [f"## {_TYPE_TITLES[t]}", "", _REQ_HEADER, *[r.row() for r in group], ""]
    f.write_text("\n".join(lines), encoding="utf-8")
    return f


_REQ_ROW = re.compile(r"^\|\s*([A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*-[A-Z]{0,3}\d{2,4})\s*\|")


def load_requirements(brd: Path, files: Optional[Iterable[Path]] = None) -> List[Requirement]:
    """The requirements written by :func:`write_requirements` (five columns)."""
    out: List[Requirement] = []
    files = list(files) if files is not None else sorted((Path(brd) / "requirements").glob("*_requirements.md"))
    for f in files:
        if not Path(f).exists():
            continue
        kind = ""
        for line in Path(f).read_text(encoding="utf-8").splitlines():
            if line.startswith("## "):
                kind = next((k for k, v in _TYPE_TITLES.items() if line[3:].strip().startswith(v)), "")
            m = _REQ_ROW.match(line)
            if not m or not kind:
                continue
            cells = _split(line)
            if len(cells) < 5:
                continue
            rid, loc, quote, reading, doc = cells[:5]
            para = quote.startswith("(paraphrase) ")
            quote = quote[len("(paraphrase) ") :] if para else quote
            status = doc.split()[0] if doc else "documented"
            q = re.search(r"\bQ-\d{2,3}\b", doc)
            out.append(Requirement(rid, kind, quote, loc, reading, paraphrase=para, status=status,
                                   question=q.group(0) if q else ""))  # fmt: skip
    return out


# ── the matrix ─────────────────────────────────────────────────────────────

_MATRIX_HEADER = (
    "| ID | Source § | Requirement (short) | Features | Screens | Code / API | Tests | Status | Evidence, gaps |\n"
    "|---|---|---|---|---|---|---|---|---|"
)


def matrix_status(brd: Path) -> Dict[str, str]:
    """ID → status, for every matrix row."""
    f = Path(brd) / MATRIX
    out: Dict[str, str] = {}
    if not f.exists():
        return out
    for line in f.read_text(encoding="utf-8").splitlines():
        m = _REQ_ROW.match(line)
        if m:
            found = [c for c in _split(line) if c in STATUSES]
            out[m.group(1)] = found[0] if len(found) == 1 else ""
    return out


def add_matrix_rows(brd: Path, reqs: Sequence[Requirement], section: str, card: str = "") -> List[str]:
    """Add a ``planned`` row for each requirement the matrix lacks, under the
    ``## <section>`` heading (made when missing). Existing rows are left as
    they are. Returns the IDs added."""
    f = Path(brd) / MATRIX
    f.parent.mkdir(parents=True, exist_ok=True)
    if not f.exists():
        f.write_text(
            "# Requirements traceability matrix\n\nThe **only** place where a requirement's implementation status "
            "is stated. Chain: requirement → feature → screen → code / API → test → evidence.\n",
            encoding="utf-8",
        )
    have = set(matrix_status(brd))
    new = [r for r in reqs if r.id not in have and r.status != "superseded"]
    if not new:
        return []
    text = f.read_text(encoding="utf-8").rstrip("\n")
    rows = [
        f"| {r.id} | {_cell(r.location)} | {_cell((r.reading or r.quote)[:120])} |  |  |  |  | planned | "
        f"{_cell(card)} |"
        for r in new
    ]
    heading = f"## {section}"
    lines = text.splitlines()
    if heading in lines:
        i = lines.index(heading) + 1
        while i < len(lines) and not lines[i].startswith("## "):
            i += 1
        while i > 0 and not lines[i - 1].strip():
            i -= 1
        lines[i:i] = rows
    else:
        lines += ["", heading, "", _MATRIX_HEADER, *rows]
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return [r.id for r in new]


def set_matrix_row(brd: Path, rid: str, status: str, tests: Sequence[str] = (), code: str = "",
                   evidence: str = "") -> bool:  # fmt: skip
    """Set one matrix row's status, with the tests that prove it.

    A row of the engine's nine columns gets its Code / API, Tests, Status
    and Evidence cells. Any other layout (a folder written by hand) keeps
    its columns: the one status cell is replaced, and the tests and the
    evidence go in the last cell. Returns whether the row was found."""
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}, not {status!r}")
    f = Path(brd) / MATRIX
    if not f.exists():
        return False
    lines = f.read_text(encoding="utf-8").splitlines()
    cited = "<br>".join(f"`{t}`" for t in tests)
    for n, line in enumerate(lines):
        m = _REQ_ROW.match(line)
        if not m or m.group(1) != rid:
            continue
        cells = _split(line)
        if len(cells) == 9:
            cells[5] = code or cells[5]
            cells[6] = cited or cells[6]
            cells[7] = status
            cells[8] = evidence or cells[8]
        else:
            at = [i for i, c in enumerate(cells) if c in STATUSES]
            if len(at) != 1:
                return False
            cells[at[0]] = status
            cells[-1] = " ".join(x for x in (cells[-1], cited, evidence) if x)
        lines[n] = "| " + " | ".join(_cell(c) for c in cells) + " |"
        f.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return True
    return False


# ── questions ──────────────────────────────────────────────────────────────


@dataclass
class Question:
    id: str
    type: str  # ambiguity | conflict | divergence | gap
    topic: str
    fact: str  # what the sources say, with where
    interpretation: str = ""  # ours, proposed
    who: str = "the owner"  # who can decide
    state: str = "open"
    resolution: str = ""
    requirements: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.type not in QUESTION_TYPES:
            raise ValueError(f"question type must be one of {QUESTION_TYPES}, not {self.type!r}")

    def entry(self) -> str:
        parts = [f"### {self.id} — {self.topic}", "", f"**Type:** {self.type} · **State:** {self.state}", "",
                 f"**Fact (sources):** {self.fact}", ""]  # fmt: skip
        if self.interpretation:
            parts += [f"**Interpretation (ours, proposed):** {self.interpretation}", ""]
        parts += [f"**Who decides:** {self.who}", ""]
        if self.requirements:
            parts += [f"**Requirements:** {', '.join(self.requirements)}", ""]
        if self.resolution:
            parts += [f"**Resolution:** {self.resolution}", ""]
        return "\n".join(parts)


def load_questions(brd: Path) -> List[Question]:
    f = Path(brd) / QUESTIONS
    if not f.exists():
        return []
    out: List[Question] = []
    for line in f.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\|\s*(Q-\d{2,3})\s*\|", line)
        if m:
            cells = _split(line)
            if len(cells) >= 4 and cells[1] in QUESTION_TYPES:
                out.append(Question(cells[0], cells[1], cells[2], fact="", state=cells[3]))
    return out


def add_questions(brd: Path, questions: Sequence[Question]) -> List[Question]:
    """Give each new question the next ``Q-nn`` and add it, to the index and
    as an entry. A question with the same topic as an existing one is not
    added twice. Nothing is ever removed."""
    f = Path(brd) / QUESTIONS
    f.parent.mkdir(parents=True, exist_ok=True)
    if not f.exists():
        f.write_text(
            "# Open questions, ambiguities, conflicts and divergences — `Q-nn`\n\nEach entry separates **what the "
            "sources say** (fact) from **our interpretation** (proposal) and records who can decide. A resolved "
            "entry stays here with its resolution; it is never deleted.\n\nTypes: **ambiguity** (a source is "
            "unclear) · **conflict** (two statements disagree) · **divergence** (the product differs from a "
            "source, knowingly) · **gap** (required, not built).\n\n| ID | Type | Topic | State |\n|---|---|---|---|\n",
            encoding="utf-8",
        )
    existing = load_questions(brd)
    topics = {" ".join(q.topic.lower().split()) for q in existing}
    n = max((int(q.id.split("-")[1]) for q in existing), default=0)
    added: List[Question] = []
    for q in questions:
        key = " ".join(q.topic.lower().split())
        if key in topics:
            continue
        n += 1
        q.id = f"Q-{n:02d}"
        topics.add(key)
        added.append(q)
    if not added:
        return []
    lines = f.read_text(encoding="utf-8").rstrip("\n").splitlines()
    idx = [i for i, ln in enumerate(lines) if re.match(r"^\|\s*(Q-\d{2,3}|---|ID)\s*\|", ln)]
    at = idx[-1] + 1 if idx else len(lines)
    lines[at:at] = [f"| {q.id} | {q.type} | {_cell(q.topic)} | {q.state} |" for q in added]
    lines += [""] + [q.entry() for q in added]
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return added


def resolve_question(brd: Path, qid: str, resolution: str, state: str = "resolved") -> bool:
    """Record a question's resolution: its index row's state, and a
    ``**Resolution:**`` line in its entry. The entry is never removed."""
    f = Path(brd) / QUESTIONS
    if not f.exists():
        return False
    lines = f.read_text(encoding="utf-8").splitlines()
    found = False
    for n, line in enumerate(lines):
        if re.match(rf"^\|\s*{re.escape(qid)}\s*\|", line):
            cells = _split(line)
            if len(cells) >= 4:
                cells[3] = state
                lines[n] = "| " + " | ".join(_cell(c) for c in cells) + " |"
        elif line.startswith(f"### {qid} "):
            found = True
            end = n + 1
            while end < len(lines) and not lines[end].startswith("### "):
                end += 1
            body = lines[n + 1 : end]
            body = [re.sub(r"\*\*State:\*\* \w+", f"**State:** {state}", b) for b in body]
            while body and not body[-1].strip():
                body.pop()
            body += ["", f"**Resolution:** {_cell(resolution)}", ""]
            lines[n + 1 : end] = body
            break
    if found:
        f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return found
