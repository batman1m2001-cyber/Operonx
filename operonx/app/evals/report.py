"""Reports of an experiment: Markdown for an MR comment, JSON, JUnit XML.

All three read an :class:`~operonx.app.evals.experiments.ExperimentData`,
so a run that just finished, a record and a row in the score store report
the same way::

    exp = load_experiment(run)
    print(markdown(exp))                    # the gate first, then why
    write_reports(exp, ["md", "junit"], "out/eval")

**Markdown** leads with the verdict and exit code, then any judge that
gates the eval without being shown to agree with people (``UNVALIDATED
JUDGE``), the reasons and warnings, the metrics with their intervals, the
judges (version, model, alignment, calls, spend), the comparison with the
baseline (and the cases that flipped), the failing and flaky cases, cost
and latency — the system's and the judges' apart. **JSON** is the experiment as data. **JUnit** is what CI
widgets read: a ``gate`` testcase (failed on ``failed``/``regressed``,
an error on ``error``, skipped on ``inconclusive`` — failed under
``strict``), then one testcase per case and check, failed unless every
repeat passed; a case whose runs errored has a ``run`` testcase with the
error. It validates against the JUnit schema GitLab's widget reads
(Jenkins xunit ``junit-10.xsd``).
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Union

from .experiments import ExperimentData
from .gate import ERROR, FAILED, INCONCLUSIVE, PASS_METRIC, REGRESSED

__all__ = [
    "FILES",
    "FORMATS",
    "as_json",
    "compare_markdown",
    "junit",
    "markdown",
    "render",
    "write_reports",
]

#: Report formats, and the file each is written to.
FILES = {"md": "report.md", "json": "experiment.json", "junit": "junit.xml"}
FORMATS = tuple(FILES)

#: How many failing cases the Markdown lists.
MAX_CASES = 10

# characters XML 1.0 cannot hold, even escaped
_XML_BAD = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")


# ── small formatting ─────────────────────────────────────────────────────


def _pct(x: Any) -> str:
    return "–" if x is None else f"{100 * float(x):.1f}%"


def _pts(x: Any) -> str:
    return "–" if x is None else f"{100 * float(x):+.1f}"


def _p(x: Any) -> str:
    if x is None:
        return "–"
    return "<0.001" if float(x) < 0.001 else f"{float(x):.3g}"


def _cell(value: Any, limit: int = 80) -> str:
    """A value for one Markdown table cell: one line, no pipes, clipped."""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    text = " ".join(str(text).split()).replace("|", "\\|")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _table(head: Sequence[str], rows: Iterable[Sequence[Any]]) -> List[str]:
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    out.extend("| " + " | ".join(str(c) for c in row) + " |" for row in rows)
    return out


def _natural(text: str) -> List[Any]:
    """``c2`` before ``c10``: digits compare as numbers."""
    return [(0, int(p), "") if p.isdigit() else (1, 0, p) for p in re.split(r"(\d+)", text) if p]


def _by_case(exp: ExperimentData) -> Dict[str, List[Dict[str, Any]]]:
    """Trials by case, cases in natural id order — the same from a record
    (dataset order) and from a store (id order)."""
    out: Dict[str, List[Dict[str, Any]]] = {}
    for item in exp.items:
        out.setdefault(str(item["case"]), []).append(item)
    for trials in out.values():
        trials.sort(key=lambda i: i.get("repeat") or 0)
    return {c: out[c] for c in sorted(out, key=_natural)}


def _headline(exp: ExperimentData) -> str:
    gate = exp.gate
    verdict = str(gate.get("verdict") or "unknown").upper()
    code = gate.get("exit_code")
    return f"## Eval `{exp.eval}`: {verdict}" + (f" (exit {code})" if code is not None else "")


def _identity(exp: ExperimentData) -> str:
    s, fp = exp.summary, exp.fingerprint
    parts = [f"experiment `{exp.experiment_id}`"]
    if fp.get("code_version"):
        parts.append(
            f"commit `{fp['code_version']}`" + (" (dirty)" if fp.get("version_dirty") else "")
        )
    dataset = Path(str(s.get("dataset") or "")).stem
    if dataset or fp.get("dataset_version"):
        parts.append(f"dataset `{dataset}` `{fp.get('dataset_version') or '?'}`")
    if s.get("selection"):
        parts.append("selection " + ", ".join(f"{k}={v}" for k, v in s["selection"].items()))
    repeats = int(s.get("repeats") or 1)
    parts.append(f"{s.get('cases', 0)} cases" + (f" × {repeats} repeats" if repeats > 1 else ""))
    if s.get("variant"):
        parts.append(f"variant “{s['variant']}”")
    return " · ".join(parts)


def _comparison_lines(comparison: Mapping[str, Any], alpha: float, ref: str = "") -> List[str]:
    out = [
        f"### Against `{comparison.get('baseline')}`{ref} — {comparison.get('cases', 0)} shared cases",
        "",
    ]
    rows = []
    for t in comparison.get("tests") or []:
        if t.get("gated"):
            name = f"{t['metric']} (gated)"
            p = f"{_p(t.get('p_holm', t.get('p')))} Holm"
            verdict = t.get("verdict") or "not judged"
        else:
            name = t["metric"]
            p = f"q={_p(t.get('q_bh'))} BH"
            verdict = "exploratory"
        rows.append(
            [
                _cell(name),
                _pct(t.get("a")),
                _pct(t.get("b")),
                f"{_pts(t.get('diff'))} pts",
                f"[{_pts(t.get('ci_lo'))}, {_pts(t.get('ci_hi'))}]",
                p,
                verdict,
            ]
        )
    level = f"{100 * (1 - alpha):g}% CI"
    out += _table(["Metric", "Baseline", "This run", "Diff", level, "p", "Verdict"], rows)
    flips = comparison.get("flips") or {}
    listed = flips.get("cases") or {}
    moved = [
        f"{flips.get(k, 0)} {k}"
        + (
            f" ({', '.join(f'`{c}`' for c in sorted(listed[k], key=_natural)[:10])}{', …' if flips.get(k, 0) > 10 else ''})"
            if listed.get(k)
            else ""
        )
        for k in ("regressed", "fixed", "destabilised", "stabilised")
        if flips.get(k)
    ]
    if moved:
        tail = "" if flips.get("verified") else " — one trial each side: changed, not verified"
        out += ["", "Flips: " + "; ".join(moved) + tail]
    for k in ("changed_cases", "only_in_baseline", "only_in_this_run"):
        if comparison.get(k):
            out.append(f"{k.replace('_', ' ')}: {comparison[k]}")
    return out


#: How a judge warning that must not be missed starts (D60).
UNVALIDATED = "UNVALIDATED JUDGE"


def _f2(x: Any) -> str:
    return "–" if x is None else f"{float(x):.2f}"


def _judges_lines(judges: Mapping[str, Any]) -> List[str]:
    rows = []
    for name, j in judges.items():
        a = j.get("alignment")
        aligned = (
            f"κ {_f2(a.get('kappa'))} · TPR {_f2(a.get('tpr'))} · TNR {_f2(a.get('tnr'))} · "
            f"n {a.get('n')}"
            if a
            else "none"
        )
        model = j.get("model")
        rows.append(
            [
                f"`{_cell(name, 60)}`",
                f"`{j.get('version') or '?'}`",
                _cell(model if isinstance(model, str) else ", ".join(model or []) or "–", 40),
                "yes" if j.get("gating") else "no",
                aligned,
                j.get("calls", 0),
                j.get("cached", 0),
                j.get("errors", 0),
                "–" if j.get("cost_usd") is None else f"${float(j['cost_usd']):.4f}",
            ]
        )
    head = ["Judge", "Version", "Model", "Gates", "Alignment", "Calls", "Cached", "Errors", "Cost"]
    return ["### Judges", ""] + _table(head, rows) + [""]


def markdown(exp: ExperimentData, *, max_cases: int = MAX_CASES) -> str:
    """The experiment as Markdown for an MR comment (see the module docstring)."""
    s, gate = exp.summary, exp.gate
    out = [_headline(exp), "", _identity(exp), ""]
    warnings = list(gate.get("warnings") or [])
    loud = [w for w in warnings if str(w).startswith(UNVALIDATED)]
    if loud:
        out += [f"> **{UNVALIDATED}** — {_cell(w[len(UNVALIDATED) :].strip(), 400)}" for w in loud]
        out += [""]
    if gate.get("reasons"):
        out += ["**Why**", ""] + [f"- {_cell(r, 400)}" for r in gate["reasons"]] + [""]
    rest = [w for w in warnings if w not in loud]
    if rest:
        out += ["**Warnings**", ""] + [f"- {_cell(w, 400)}" for w in rest] + [""]

    metrics = exp.metrics
    if metrics:
        names = [PASS_METRIC] + sorted(m for m in metrics if m != PASS_METRIC)
        rows = [
            [
                _cell(m),
                _pct(metrics[m].get("mean")),
                f"{_pct(metrics[m].get('ci_lo'))} – {_pct(metrics[m].get('ci_hi'))}",
                metrics[m].get("n", "–"),
                metrics[m].get("method", "–"),
            ]
            for m in names
            if m in metrics
        ]
        out += (
            ["### Metrics", ""] + _table(["Metric", "Mean", "95% CI", "n", "Method"], rows) + [""]
        )

    if s.get("judges"):
        out += _judges_lines(s["judges"])

    comparison = gate.get("comparison")
    if comparison:
        ref = f" ({comparison['baseline_ref']})" if comparison.get("baseline_ref") else ""
        alpha = float((gate.get("gate") or {}).get("alpha") or 0.05)
        out += _comparison_lines(comparison, alpha, ref) + [""]

    cases = _by_case(exp)
    failing = {c: t for c, t in cases.items() if not all(i.get("passed") for i in t)}
    if failing:
        shown = list(failing.items())[:max_cases]
        title = f"### Failing cases ({len(failing)} of {len(cases)})"
        if len(failing) > max_cases:
            title += f" — the first {max_cases}"
        rows = []
        for case, trials in shown:
            bad = []
            for t in trials:
                if t.get("error") and not t.get("checks"):
                    bad.append(f"run: {t['error']}")
                for name, check in (t.get("checks") or {}).items():
                    if not check.get("passed"):
                        why = check.get("reason") or check.get("error") or ""
                        bad.append(f"{name}" + (f": {why}" if why else ""))
            first = next((t for t in trials if not t.get("passed")), trials[0])
            rows.append(
                [
                    f"`{_cell(case, 60)}`",
                    f"{sum(1 for t in trials if t.get('passed'))}/{len(trials)}",
                    _cell("; ".join(dict.fromkeys(bad)) or "–", 160),
                    f"`{_cell(first.get('output'), 80)}`"
                    if first.get("output") is not None
                    else "–",
                ]
            )
        out += [title, ""] + _table(["Case", "Passed", "Failed checks", "Output"], rows) + [""]

    rel = s.get("reliability") or {}
    if rel.get("flaky"):
        ids = ", ".join(f"`{c}`" for c in (rel.get("flaky_cases") or [])[:20])
        hat = ", ".join(f"pass^{k} {_pct(v)}" for k, v in (rel.get("pass_hat_k") or {}).items())
        out += [f"**Flaky:** {rel['flaky']} case(s) — {ids}" + (f" · {hat}" if hat else ""), ""]

    ops = []
    if s.get("cost_usd") is not None:
        ops.append(f"system ${float(s['cost_usd']):.4f}")
    if s.get("judge_cost_usd") is not None:
        ops.append(f"judges ${float(s['judge_cost_usd']):.4f}")
    if s.get("p50_ms") is not None:
        ops.append(f"p50 {float(s['p50_ms']):.0f} ms")
    if s.get("p95_ms") is not None:
        ops.append(f"p95 {float(s['p95_ms']):.0f} ms")
    if s.get("errored"):
        ops.append(f"{s['errored']} errored")
    if ops:
        out += ["Cost and latency: " + " · ".join(ops), ""]
    return "\n".join(out).rstrip() + "\n"


def compare_markdown(result: Mapping[str, Any]) -> str:
    """A :func:`~operonx.app.evals.compare.compare` result as Markdown."""
    verdict = result.get("verdict")
    head = f"## Compare `{result['baseline']}` → `{result['candidate']}`"
    if verdict:
        head += f": {str(verdict).upper()} (exit {result['exit_code']})"
    out = [head, ""]
    if result.get("reasons"):
        out += ["**Why**", ""] + [f"- {_cell(r, 400)}" for r in result["reasons"]] + [""]
    if result.get("warnings"):
        out += ["**Warnings**", ""] + [f"- {_cell(w, 400)}" for w in result["warnings"]] + [""]
    out += _comparison_lines(result["comparison"], float(result.get("alpha") or 0.05))
    if result.get("pairwise"):
        out += [""] + _pairwise_lines(result["pairwise"])
    return "\n".join(out).rstrip() + "\n"


def _pairwise_lines(got: Mapping[str, Any]) -> List[str]:
    """``compare_pairwise``'s result: per judge, who won, how often the
    order decided, what it cost."""
    out = [f"### Pairwise — {got.get('cases', 0)} cases judged in both orders", ""]
    rows, notes = [], []
    for name, j in (got.get("judges") or {}).items():
        pref = j.get("preference") or {}
        rows.append(
            [
                f"`{_cell(name, 60)}`",
                j.get("wins_a", 0),
                j.get("wins_b", 0),
                j.get("ties", 0),
                _pct(j.get("inconsistency_rate")),
                f"{_pct(pref.get('mean'))} [{_pct(pref.get('ci_lo'))}, {_pct(pref.get('ci_hi'))}]"
                if pref
                else "–",
                "–" if j.get("cost_usd") is None else f"${float(j['cost_usd']):.4f}",
            ]
        )
        notes += j.get("warnings") or []
    head = [
        "Judge",
        "Baseline wins",
        "This run wins",
        "Ties",
        "Order flipped",
        "Preference",
        "Cost",
    ]
    out += _table(head, rows)
    if got.get("skipped"):
        out += ["", f"skipped: {len(got['skipped'])} case(s)"]
    if notes:
        out += ["", "**Warnings**", ""] + [f"- {_cell(w, 400)}" for w in notes]
    return out


def as_json(exp: ExperimentData) -> str:
    return json.dumps(exp.as_dict(), indent=2, ensure_ascii=False, default=str) + "\n"


# ── JUnit ────────────────────────────────────────────────────────────────


def _xml(text: Any, limit: int = 4000) -> str:
    text = text if isinstance(text, str) else json.dumps(text, ensure_ascii=False, default=str)
    text = _XML_BAD.sub("", text)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _seconds(ms: Optional[float]) -> str:
    return f"{max(0.0, float(ms or 0.0)) / 1000:.3f}"


def junit(exp: ExperimentData) -> str:
    """The experiment as JUnit XML (see the module docstring)."""
    gate = exp.gate
    verdict = gate.get("verdict")
    cases: List[ET.Element] = []

    tc = ET.Element("testcase", classname=exp.eval, name="gate", time="0.000")
    message = f"{verdict}: exit {gate.get('exit_code')}"
    body = "\n".join([*(gate.get("reasons") or []), *(gate.get("warnings") or [])])
    if verdict == ERROR:
        ET.SubElement(tc, "error", message=_xml(message, 200), type=ERROR).text = _xml(body)
    elif verdict in (FAILED, REGRESSED) or (verdict == INCONCLUSIVE and gate.get("strict")):
        ET.SubElement(tc, "failure", message=_xml(message, 200), type=str(verdict)).text = _xml(
            body
        )
    elif verdict == INCONCLUSIVE:
        ET.SubElement(tc, "skipped", message=_xml(message, 200)).text = _xml(body)
    cases.append(tc)

    for case, trials in _by_case(exp).items():
        classname = f"{exp.eval}.{case}"
        n = len(trials)
        time = _seconds(sum(t.get("ms") or 0 for t in trials) / n)
        errored = [t for t in trials if t.get("error") and t.get("status") not in ("ok", "empty")]
        if errored:
            tc = ET.Element("testcase", classname=classname, name="run", time=time)
            ET.SubElement(
                tc, "error", message=_xml(f"{len(errored)}/{n} runs errored", 200), type="run"
            ).text = _xml("\n".join(str(t["error"]) for t in errored))
            cases.append(tc)
        names = list(dict.fromkeys(k for t in trials for k in (t.get("checks") or {})))
        if not names and not errored:
            names = [PASS_METRIC]
        for name in names:
            tc = ET.Element("testcase", classname=classname, name=name, time=time)
            judged = []  # (trial, its verdict on this check); errored trials have none
            for trial in trials:
                checks = trial.get("checks") or {}
                if name in checks:
                    judged.append((trial, checks[name]))
                elif name == PASS_METRIC and not checks and trial not in errored:
                    judged.append((trial, {"passed": trial.get("passed")}))
            passed = sum(1 for _, c in judged if c.get("passed"))
            if passed < len(judged):
                why = [
                    f"repeat {trial.get('repeat', 0)}: "
                    + str(c.get("reason") or c.get("error") or "failed")
                    for trial, c in judged
                    if not c.get("passed")
                ]
                flaky = " (flaky)" if passed else ""
                ET.SubElement(
                    tc,
                    "failure",
                    message=_xml(f"{passed}/{len(judged)} repeats passed{flaky}", 200),
                    type="check",
                ).text = _xml("\n".join(why))
            cases.append(tc)

    failures = sum(1 for c in cases if c.find("failure") is not None)
    errors = sum(1 for c in cases if c.find("error") is not None)
    skipped = sum(1 for c in cases if c.find("skipped") is not None)
    total_ms = sum(t.get("ms") or 0 for t in exp.items)
    suite = ET.Element(
        "testsuite",
        name=f"eval:{exp.eval}",
        tests=str(len(cases)),
        failures=str(failures),
        errors=str(errors),
        skipped=str(skipped),
        time=_seconds(total_ms),
        timestamp=str(exp.started or ""),
        id=exp.experiment_id,
    )
    props = ET.SubElement(suite, "properties")
    fp = exp.fingerprint
    for key, value in (
        ("experiment", exp.experiment_id),
        ("verdict", verdict),
        ("exit_code", gate.get("exit_code")),
        ("code_version", fp.get("code_version")),
        ("version_dirty", fp.get("version_dirty")),
        ("dataset_version", fp.get("dataset_version")),
        ("evaluators_hash", fp.get("evaluators_hash")),
        ("pass_rate", exp.summary.get("pass_rate")),
        ("cost_usd", exp.summary.get("cost_usd")),
        ("judge_cost_usd", exp.summary.get("judge_cost_usd")),
    ):
        if value is not None:
            ET.SubElement(props, "property", name=key, value=_xml(str(value), 200))
    suite.extend(cases)
    root = ET.Element(
        "testsuites",
        name=f"operonx eval {exp.eval}",
        tests=str(len(cases)),
        failures=str(failures),
        errors=str(errors),
        time=_seconds(total_ms),
    )
    root.append(suite)
    ET.indent(root)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


# ── by name ──────────────────────────────────────────────────────────────


def render(exp: ExperimentData, fmt: str) -> str:
    """*exp* in *fmt*: ``md``, ``json`` or ``junit``."""
    if fmt == "md":
        return markdown(exp)
    if fmt == "json":
        return as_json(exp)
    if fmt == "junit":
        return junit(exp)
    raise ValueError(f"no report format {fmt!r}; one of {', '.join(FORMATS)}")


def parse_formats(text: Union[str, Sequence[str]]) -> List[str]:
    """``"md,junit"`` → ``["md", "junit"]``, each checked."""
    items = text.split(",") if isinstance(text, str) else list(text)
    out = [f.strip() for f in items if f.strip()]
    unknown = [f for f in out if f not in FILES]
    if unknown:
        raise ValueError(f"no report format {unknown}; formats are {', '.join(FORMATS)}")
    return out


def write_reports(
    exp: ExperimentData, formats: Union[str, Sequence[str]], out_dir: Union[str, Path]
) -> Dict[str, Path]:
    """Write *exp* in each of *formats* under *out_dir* (``report.md``,
    ``experiment.json``, ``junit.xml``); returns the paths by format."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written: Dict[str, Path] = {}
    for fmt in parse_formats(formats):
        path = out / FILES[fmt]
        path.write_text(render(exp, fmt), encoding="utf-8")
        written[fmt] = path
    return written
