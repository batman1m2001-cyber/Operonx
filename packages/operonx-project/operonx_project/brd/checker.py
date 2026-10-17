"""``operonx-project brd check``: the living BRD's checker, generalized (plan §4.4).

OneWealth's ``scripts/check_brd_docs.py`` with its constants moved into
``<brd>/brd.toml``; its messages are kept word for word, so the two can be
compared on OneWealth's own folder. Each check is an error when it fails:

- every relative link resolves to a file, and every ``#anchor`` to a heading;
- every requirement defined in ``requirements/`` has exactly one matrix row,
  and every row's ID is defined; the required codes are all defined;
- every status is in the vocabulary, and ``implemented_verified`` cites a test;
- every test cited as ``tests/<file>.py::<name>`` exists;
- the matrix's summary table equals the counts of its rows;
- every FEAT-, SCR-, ADR-, Q- and DEF- id referenced is defined;
- the verbatim source copies still have their recorded SHA-256;
- every file under ``assets/`` is registered in ``assets/README.md``.

With a source file (``--source SRC-01=path``): its SHA-256 is the registered
one, and every code the config's pattern finds in it is defined.

``brd.toml`` (every key optional)::

    root = "../.."                  # what paths in messages are relative to
    tests = "../../tests"           # where cited tests live
    requirement_id = '(?:BRD-...)'  # a regex for requirement ids
    required = ["BRD-BR-01"]        # ids that must be defined
    skip = ["sources/redesign_kit"] # Markdown not link-checked
    screen_id = 'SCR-(?:CL|RM)-\\d{2}'
    [[groups]]                       # the summary table's rows
    name = "BRD numbered (BR, FR, NFR)"
    pattern = 'BRD-(?:BR|FR|NFR)-'
    [copies]                         # verbatim copies and their SHA-256
    "sources/kit/README.md" = "5852cd…"
    [codes]                          # what --source checks in a source file
    pattern = '\\b(BR-\\d{2}|FR-\\d{3})\\b'
    prefix = "BRD-"
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from operonx_project.brd.records import MATRIX, STATUSES

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]

__all__ = ["BrdConfig", "check", "default_requirement_id"]

_NOT_REQ = "(?!(?:FEAT|SCR|ADR|Q|DEF|SRC)-)"
default_requirement_id = _NOT_REQ + r"[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*-[A-Z]{0,3}\d{2,4}"
LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)\)|!\[[^\]]*\]\(([^)\s]+)\)")
TEST_REF = re.compile(r"tests/((?:\w+/)?test_\w+\.py)::(test_\w+)")


@dataclass
class BrdConfig:
    brd: Path
    root: Path
    tests: Path
    requirement_id: str = default_requirement_id
    required: List[str] = field(default_factory=list)
    groups: List[Tuple[str, str]] = field(default_factory=list)
    skip: List[str] = field(default_factory=list)
    screen_id: str = r"SCR-[A-Z]+-\d{2}"
    copies: Dict[str, str] = field(default_factory=dict)
    codes_pattern: str = ""
    codes_prefix: str = ""

    @property
    def matrix(self) -> Path:
        return self.brd / MATRIX

    @classmethod
    def load(cls, brd: Path) -> "BrdConfig":
        brd = Path(brd).resolve()
        f = brd / "brd.toml"
        data = tomllib.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
        root = (brd / data.get("root", "../..")).resolve()
        cfg = cls(
            brd=brd,
            root=root,
            tests=(brd / data.get("tests", "../../tests")).resolve(),
            requirement_id=data.get("requirement_id", default_requirement_id),
            required=list(data.get("required", [])),
            groups=[(g["name"], g["pattern"]) for g in data.get("groups", [])],
            skip=list(data.get("skip", [])),
            screen_id=data.get("screen_id", r"SCR-[A-Z]+-\d{2}"),
            copies=dict(data.get("copies", {})),
            codes_pattern=(data.get("codes") or {}).get("pattern", ""),
            codes_prefix=(data.get("codes") or {}).get("prefix", ""),
        )
        return cfg

    def rel(self, p: Path) -> str:
        try:
            return str(p.resolve().relative_to(self.root))
        except ValueError:
            return str(p)


def slug(heading: str) -> str:
    """GitHub's anchor for a heading: lower case, punctuation dropped, spaces to hyphens."""
    text = re.sub(r"`|\*|_(?=\W)|\[([^\]]*)\]\([^)]*\)", r"\1", heading.strip()).lower()
    text = unicodedata.normalize("NFC", text)
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def anchors(path: Path) -> Set[str]:
    seen: Counter = Counter()
    out = set()
    in_code = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("```"):
            in_code = not in_code
            continue
        m = None if in_code else re.match(r"#{1,6}\s+(.*)", line)
        if m:
            base = slug(m.group(1))
            out.add(base if not seen[base] else f"{base}-{seen[base]}")
            seen[base] += 1
    return out


def md_files(cfg: BrdConfig) -> List[Path]:
    return sorted(p for p in cfg.brd.rglob("*.md") if not any(s in p.as_posix() for s in cfg.skip))


def check_links(cfg: BrdConfig, errors: List[str]) -> None:
    cache: Dict[Path, Set[str]] = {}
    for md in md_files(cfg):
        text = md.read_text(encoding="utf-8")
        text = re.sub(r"```.*?```", "", text, flags=re.S)
        for m in LINK.finditer(text):
            target = m.group(1) or m.group(2)
            if re.match(r"[a-z]+:", target):
                continue  # http:, https:, mailto:
            file_part, _, anchor = target.partition("#")
            dest = (md.parent / file_part).resolve() if file_part else md
            where = f"{cfg.rel(md)} → {target}"
            if not dest.exists():
                errors.append(f"broken link: {where}")
                continue
            if anchor and dest.suffix == ".md":
                if dest not in cache:
                    cache[dest] = anchors(dest)
                if anchor not in cache[dest]:
                    errors.append(f"missing anchor: {where}")


def defined_requirements(cfg: BrdConfig) -> Set[str]:
    ids: Set[str] = set()
    for md in (cfg.brd / "requirements").glob("*.md"):
        if md == cfg.matrix:
            continue
        for line in md.read_text(encoding="utf-8").splitlines():
            m = re.match(rf"(?:###\s+|\|\s*)({cfg.requirement_id})\b", line)
            if m:
                ids.add(m.group(1))
    return ids


def matrix_rows(cfg: BrdConfig) -> Dict[str, str]:
    rows: Dict[str, str] = {}
    dup = []
    if not cfg.matrix.exists():
        return rows
    for line in cfg.matrix.read_text(encoding="utf-8").splitlines():
        m = re.match(rf"\|\s*({cfg.requirement_id})\s*\|", line)
        if m:
            if m.group(1) in rows:
                dup.append(m.group(1))
            rows[m.group(1)] = line
    if dup:
        raise SystemExit(f"duplicate matrix rows: {dup}")
    return rows


def row_status(row: str) -> str:
    found = [c.strip() for c in row.split("|") if c.strip() in STATUSES]
    return found[0] if len(found) == 1 else ""


def tests_index(cfg: BrdConfig) -> Dict[str, Set[str]]:
    out: Dict[str, Set[str]] = {}
    if not cfg.tests.is_dir():
        return out
    for f in cfg.tests.rglob("test_*.py"):
        names = re.findall(r"^(?:async\s+)?def\s+(test_\w+)", f.read_text(encoding="utf-8"), re.M)
        out[f.relative_to(cfg.tests).as_posix()] = set(names)
    return out


def _groups(cfg: BrdConfig, ids: Set[str]) -> List[Tuple[str, str]]:
    if cfg.groups:
        return cfg.groups
    prefixes = sorted({i.split("-")[0] for i in ids})
    return [(p, re.escape(p) + "-") for p in prefixes]


def check_requirements(cfg: BrdConfig, errors: List[str], report: List[str]) -> None:
    defined = defined_requirements(cfg)
    rows = matrix_rows(cfg)
    for code in cfg.required:
        if code not in defined:
            errors.append(f"BRD code {code.removeprefix(cfg.codes_prefix)} has no extracted requirement")
    for rid in sorted(defined - set(rows)):
        errors.append(f"{rid} is defined but has no matrix row")
    for rid in sorted(set(rows) - defined):
        errors.append(f"matrix row {rid} is not defined in requirements/")
    tests = tests_index(cfg)
    sets = _groups(cfg, defined | set(rows))
    counts = {name: Counter() for name, _ in sets}
    for rid, row in rows.items():
        status = row_status(row)
        if not status:
            errors.append(f"{rid}: no single valid status in its row")
            continue
        refs = TEST_REF.findall(row)
        if status == "implemented_verified" and not refs:
            errors.append(f"{rid}: implemented_verified without a cited test")
        for name, pattern in sets:
            if re.match(pattern, rid):
                counts[name][status] += 1
    summary = {}
    if cfg.matrix.exists():
        for line in cfg.matrix.read_text(encoding="utf-8").splitlines():
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if cells and cells[0] in counts and len(cells) == 2 + len(STATUSES):
                summary[cells[0]] = cells[1:]
    for name, c in counts.items():
        want = [str(sum(c.values()))] + [str(c[s]) for s in STATUSES]
        report.append(f"{name}: {dict(c)} (total {sum(c.values())})")
        if summary.get(name) != want:
            errors.append(f"summary row “{name}” is {summary.get(name)}, the rows say {want}")
    for md in md_files(cfg):
        for f, name in TEST_REF.findall(md.read_text(encoding="utf-8")):
            if name not in tests.get(f, set()):
                errors.append(f"{cfg.rel(md)} cites a missing test: tests/{f}::{name}")


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8") if p.exists() else ""


def check_references(cfg: BrdConfig, errors: List[str]) -> None:
    b = cfg.brd
    feats = {"-".join(p.name.split("-")[:2]) for p in (b / "features").glob("FEAT-*.md")}
    screens = set(re.findall(cfg.screen_id, _read(b / "ux" / "screen_inventory.md")))
    adrs = {p.name[:8] for p in (b / "decisions").glob("ADR-*.md")}
    questions = set(re.findall(r"^### (Q-\d{2})", _read(b / "requirements" / "open_questions.md"), re.M))
    defects = set(re.findall(r"^\| (DEF-\d{2}) \|", _read(b / "testing" / "regression_coverage.md"), re.M))
    for md in md_files(cfg):
        text = md.read_text(encoding="utf-8")
        rel = cfg.rel(md)
        for f in set(re.findall(r"\bFEAT-(\d{2})\b", text)):
            if f"FEAT-{f}" not in feats:
                errors.append(f"{rel}: FEAT-{f} has no feature file")
        for s in set(re.findall(rf"\b{cfg.screen_id}\b", text)):
            if s not in screens:
                errors.append(f"{rel}: {s} is not in the screen inventory")
        for a in set(re.findall(r"\bADR-\d{4}\b", text)):
            if a not in adrs:
                errors.append(f"{rel}: {a} has no decision file")
        for q in set(re.findall(r"\bQ-\d{2}\b", text)):
            if q not in questions:
                errors.append(f"{rel}: {q} is not in open_questions.md")
        for d in set(re.findall(r"\bDEF-\d{2}\b", text)):
            if d not in defects:
                errors.append(f"{rel}: {d} is not in the defect list")


def check_sources_and_assets(cfg: BrdConfig, errors: List[str]) -> None:
    for name, digest in cfg.copies.items():
        p = cfg.brd / name
        if not p.exists() or hashlib.sha256(p.read_bytes()).hexdigest() != digest:
            errors.append(f"source copy changed or missing: {cfg.rel(p)}")
    assets = cfg.brd / "assets"
    if not assets.is_dir():
        return
    register = _read(assets / "README.md")
    for f in assets.rglob("*"):
        if f.is_file() and f.name != "README.md" and f.name not in register:
            errors.append(f"asset not registered in assets/README.md: {cfg.rel(f)}")


def _docx_text(path: Path) -> str:
    try:
        xml = zipfile.ZipFile(path).read("word/document.xml").decode("utf-8")
        return re.sub(r"<[^>]+>", " ", xml)
    except (zipfile.BadZipFile, KeyError):
        return path.read_text(encoding="utf-8", errors="replace")


def check_source(cfg: BrdConfig, sid: str, path: Path, errors: List[str], report: List[str]) -> None:
    from operonx_project.brd.register import load_register

    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    row = next((s for s in load_register(cfg.brd) if s.id == sid), None)
    registered = row.sha256 if row else []
    ok = digest in registered
    if not ok:
        errors.append(
            f"the BRD's SHA-256 is {digest}, registered {registered[0] if registered else None}: "
            "a new version must be registered"
        )
    if cfg.codes_pattern:
        text = _docx_text(path) if path.suffix.lower() == ".docx" else path.read_text(errors="replace")
        codes = set(re.findall(cfg.codes_pattern, text))
        defined = defined_requirements(cfg) | set(cfg.required)
        missing = sorted(c for c in codes if f"{cfg.codes_prefix}{c}" not in defined)
        if missing:
            errors.append(f"codes in the BRD not extracted: {missing}")
        report.append(f"BRD file checked: {len(codes)} codes, SHA-256 {'ok' if ok else 'CHANGED'}")


def check(brd: Path, sources: Optional[Dict[str, Path]] = None) -> Tuple[List[str], List[str]]:
    cfg = BrdConfig.load(brd)
    errors: List[str] = []
    report: List[str] = []
    check_links(cfg, errors)
    check_requirements(cfg, errors, report)
    check_references(cfg, errors)
    check_sources_and_assets(cfg, errors)
    if sources:
        for sid, p in sources.items():
            check_source(cfg, sid, Path(p), errors, report)
    else:
        report.append("BRD file not given (--brd): its hash and codes were not checked")
    return errors, report
