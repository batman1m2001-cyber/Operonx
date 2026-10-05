"""`operonx eval` — run experiments, compare them, report them, size them.

    operonx eval list                                  # evals, their cases, the last verdict
    operonx eval run labels                            # one experiment; exits with the gate's code
    operonx eval run labels --baseline main --tolerance 0.03 --strict \\
                            --report md,junit --out out/eval
    operonx eval run labels --repeats 3 --split smoke --tag critical --cases c1,c2 --sample 50
    operonx eval compare <expA> <expB> [--tolerance 0.03] [--pairwise judges:helpful]
    operonx eval report <exp> [--format md|json|junit] [--out FILE]
    operonx eval rescore <exp> [--evaluators checks:strict,checks:budget]
    operonx eval calibrate labels --runs 3 [--tolerance 0.03]
    operonx eval power labels --delta 0.05 [--discordance 0.10]
    operonx eval align judge:polite [--human review] [--version V]   # κ, TPR, TNR vs people
    operonx eval dataset validate|stats|diff labels [--against main]

An eval is named as ``operonx run`` names a job: one the project declares,
or ``module:attr``. An experiment is a run id (found in the evals' record
directories, then in the project's score store) or a record directory.

``run`` writes its experiment to the project's score store —
``[evals] scores``, else the ClickHouse sink of ``[tracing]``, else files
under the runs root (:func:`~operonx.telemetry.scores.project_score_store`)
— unless the eval names its own or ``--no-store`` is given: that is how a
merge request's pipeline finds the experiment main produced.

Exit status — ``run``, and ``compare`` with a ``--tolerance``:

    0  pass (and inconclusive, with a warning)
    1  failed or regressed
    2  inconclusive under --strict; or the command could not run as asked
       (an unknown eval, a bad flag, no baseline at the merge-base, a
       store that cannot be opened) — the message on stderr says which
    3  an infrastructure error: retry the job, the quality is unknown

``align`` exits 0 when the judge agrees with the human labels (κ ≥ 0.6),
1 when it was measured and does not, 2 when there was nothing to measure.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import os
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from operonx.app import Application, ManifestError

__all__ = ["main"]


class _Usage(Exception):
    """The command cannot run as asked: printed as ``error:``, exit 2."""


# ── the project ──────────────────────────────────────────────────────────


class _Project:
    """The application (when there is a manifest), its root, and its
    stores — each opened the first time it is needed."""

    def __init__(self, manifest: Optional[str]):
        from operonx.app.manifest import MANIFEST_FILENAME

        self.app: Optional[Application] = None
        if manifest:
            self.app = Application.load(manifest)
        else:  # no manifest is a project too: evals named as module:attr
            here = Path.cwd().resolve()
            found = next(
                (d for d in (here, *here.parents) if (d / MANIFEST_FILENAME).is_file()), None
            )
            if found is not None:
                self.app = Application.find(found)
        self.root = self.app.root if self.app is not None else Path.cwd()
        self._scores: Any = None

    # -- evals -------------------------------------------------------------

    def eval(self, name: str) -> Any:
        from operonx.app.evals import Eval
        from operonx.app.serve.registry import load_object

        if ":" in name:
            cwd = os.getcwd()
            if cwd not in sys.path:
                sys.path.insert(0, cwd)
            if self.app is not None:
                self.app.bootstrap()
            found = load_object(name, field="eval")
        elif self.app is None:
            raise _Usage(f"no operonx.toml at or above {Path.cwd()}: name the eval as module:attr")
        else:
            found = self.app.job(name)
        if not isinstance(found, Eval):
            raise _Usage(f"{name!r} is a {type(found).__name__}, not an Eval")
        return found

    def evals(self) -> List[Dict[str, Any]]:
        """Every eval, described without running it: name, dataset path,
        repeats, record dir. A TOML project is read without its code."""
        from operonx.app.evals import Eval, dataset_path

        out: List[Dict[str, Any]] = []
        if self.app is None:
            return out
        specs = self.app.manifest.jobs
        if specs:
            for spec in specs:
                opts = spec.options
                if opts.get("dataset") is None:
                    continue
                record = Path(spec.record_dir or "evals")
                out.append(
                    {
                        "name": spec.name,
                        "dataset": dataset_path(opts["dataset"], self.root),
                        "repeats": int(opts.get("repeats") or 1),
                        "record_dir": record if record.is_absolute() else self.root / record,
                    }
                )
            return out
        for job in self.app.jobs:
            if isinstance(job, Eval):
                out.append(
                    {
                        "name": job.name,
                        "dataset": job.dataset.path,
                        "repeats": job.repeats,
                        "record_dir": Path(job.record_dir),
                    }
                )
        return out

    def record_dirs(self) -> List[Path]:
        dirs = [Path(e["record_dir"]) for e in self.evals()]
        dirs.append(self.root / "evals")
        return list(dict.fromkeys(d.resolve() for d in dirs if d.is_dir()))

    # -- stores --------------------------------------------------------------

    def score_source(self) -> Any:
        from operonx.telemetry.scores import project_score_store

        return project_score_store(self.root)

    def scores(self) -> Any:
        """The project's score store, opened once; an unopenable one is a
        usage error naming why."""
        if self._scores is None:
            src = self.score_source()
            if not src.openable:
                raise _Usage(
                    f"the project's score store ({src.source}) cannot be opened: {src.reason}. "
                    "Fix it, or pass --no-store"
                )
            self._scores = src.open()
        return self._scores

    def run_store(self) -> Any:
        """Where the evals' runs are traced: the project's first readable
        trace store, else the local files under the runs root."""
        from operonx.telemetry.runs import open_run_store, project_stores
        from operonx.telemetry.runs.project import project_files

        for src in project_stores(self.root):
            if src.readable:
                return src.open()
        _, resolver = project_files(self.root)
        return open_run_store({"backend": "files", "root": str(resolver.runs_root)})

    def experiment(self, ref: str, *, use_store: bool = True) -> Any:
        """An experiment by id or record path: records first, then the store."""
        from operonx.app.evals.experiments import load_experiment

        try:
            return load_experiment(ref, record_dirs=self.record_dirs())
        except ValueError as first:
            if not use_store:
                raise _Usage(str(first)) from None
        try:
            return load_experiment(ref, store=self.scores(), record_dirs=self.record_dirs())
        except ValueError as exc:
            raise _Usage(str(exc)) from None

    def close(self) -> None:
        if self._scores is not None:
            self._scores.close()


# ── parsing helpers ──────────────────────────────────────────────────────


def _tolerance(values: Optional[Sequence[str]]) -> Any:
    """``--tolerance 0.03`` (the pass rate) or ``--tolerance 'exact(x)=0.03'``
    (repeatable); ``None`` when not given."""
    if not values:
        return None
    if len(values) == 1 and "=" not in values[0]:
        return _number(values[0], "--tolerance")
    out: Dict[str, float] = {}
    for value in values:
        metric, sep, num = value.rpartition("=")
        if not sep or not metric:
            raise _Usage(f"--tolerance {value!r}: a number, or METRIC=NUMBER per metric")
        out[metric] = _number(num, "--tolerance")
    return out


def _number(text: str, flag: str) -> float:
    try:
        return float(text)
    except ValueError:
        raise _Usage(f"{flag} wants a number, not {text!r}") from None


def _csv(text: Optional[str]) -> List[str]:
    return [x.strip() for x in (text or "").split(",") if x.strip()]


def _formats(text: Optional[str]) -> List[str]:
    from operonx.app.evals.report import parse_formats

    try:
        return parse_formats(text or "")
    except ValueError as exc:
        raise _Usage(str(exc)) from None


def _emit(text: str, out: Optional[str]) -> None:
    if out:
        path = Path(out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"written: {path}", file=sys.stderr)
    else:
        sys.stdout.write(text)


# ── run ──────────────────────────────────────────────────────────────────


def _configure(ev: Any, args: argparse.Namespace) -> None:
    """Command-line overrides onto an Eval."""
    from operonx.app.evals import Gate

    if args.repeats is not None:
        if args.repeats < 1:
            raise _Usage("--repeats is a whole number of runs per case, ≥ 1")
        ev.repeats = args.repeats
    if args.split or args.tag or args.cases or args.sample:
        try:
            ev.dataset = ev.dataset.select(
                split=args.split,
                tags=args.tag or None,
                ids=_csv(args.cases) or None,
                sample=args.sample,
            )
            if not ev.dataset.rows():  # an unknown id raises here, before any record
                raise _Usage(f"the selection leaves no case of {ev.dataset.path}")
        except ValueError as exc:
            raise _Usage(str(exc)) from None
    if args.variant:
        ev.variant = args.variant
    if args.concurrency:
        ev.concurrency = args.concurrency
    if args.no_cache:
        ev.judge_cache = False
    tolerance = _tolerance(args.tolerance)
    if args.baseline or tolerance is not None or args.strict:
        gate = ev.gate or Gate(threshold=ev.threshold)
        ev.threshold = None  # with a gate, the threshold lives in it
        changes: Dict[str, Any] = {}
        if args.baseline:
            changes["baseline"] = args.baseline
        if tolerance is not None:
            if not (args.baseline or gate.baseline):
                raise _Usage("--tolerance compares against a baseline: give --baseline too")
            changes["tolerance"] = tolerance
        if args.strict:
            changes["strict"] = True
        try:
            ev.gate = dataclasses.replace(gate, **changes)
        except (TypeError, ValueError) as exc:
            raise _Usage(str(exc)) from None


def _cmd_run(project: _Project, args: argparse.Namespace) -> int:
    from operonx.app.evals.experiments import load_experiment
    from operonx.app.evals.report import write_reports
    from operonx.cli.run import outcome

    formats = _formats(args.report) if args.report else []
    ev = project.eval(args.eval)
    _configure(ev, args)
    stored = None
    if ev.scores is None and not args.no_store:
        ev.scores = project.scores()
        stored = project.score_source().describe()
    try:
        run = ev.run_sync()
    except ValueError as exc:  # a baseline that is not there: nothing ran
        raise _Usage(str(exc)) from None
    print(run.summary())
    print(f"  {run.path}")
    code = outcome(run, args.failures)
    comparison = ((run.meta.get("eval") or {}).get("gate") or {}).get("comparison")
    if comparison:
        ref = f" ({comparison['baseline_ref']})" if comparison.get("baseline_ref") else ""
        print(f"  baseline: {comparison['baseline']}{ref}")
    if stored:
        print(f"  experiment stored in {stored}")
    if formats:
        out = Path(args.out) if args.out else run.path
        for path in write_reports(load_experiment(run), formats, out).values():
            print(f"  report: {path}")
    return code


# ── compare, report ──────────────────────────────────────────────────────


def _cmd_compare(project: _Project, args: argparse.Namespace) -> int:
    from operonx.app.evals.compare import compare
    from operonx.app.evals.report import compare_markdown

    a = project.experiment(args.a, use_store=not args.no_store)
    b = project.experiment(args.b, use_store=not args.no_store)
    try:
        got = compare(
            a,
            b,
            tolerance=_tolerance(args.tolerance),
            metrics=_csv(args.metrics) or None,
            alpha=args.alpha,
            strict=args.strict,
        )
    except (TypeError, ValueError) as exc:
        raise _Usage(str(exc)) from None
    if args.pairwise:
        got["pairwise"] = _pairwise(project, a, b, args)
    if args.format == "json":
        _emit(json.dumps(got, indent=2, default=str) + "\n", args.out)
    else:
        _emit(compare_markdown(got), args.out)
    return int(got["exit_code"]) if got["exit_code"] is not None else 0


def _load_objects(project: _Project, text: str, field: str) -> List[Any]:
    from operonx.app.serve.registry import load_object

    if project.app is not None:
        project.app.bootstrap()
    cwd = os.getcwd()
    if cwd not in sys.path:
        sys.path.insert(0, cwd)
    return [load_object(e, field=field) for e in _csv(text)]


def _pairwise(project: _Project, a: Any, b: Any, args: argparse.Namespace) -> Dict[str, Any]:
    """``--pairwise``: the judges' preferences between the two experiments."""
    import asyncio

    from operonx.app.evals import compare_pairwise

    judges = _load_objects(project, args.pairwise, "--pairwise")
    store = None if args.no_store else project.scores()
    try:
        return asyncio.run(
            compare_pairwise(
                a,
                b,
                judges,
                scores=store,
                trace=[project.run_store()],
                judge_cache=store,
            )
        )
    except (TypeError, ValueError) as exc:
        raise _Usage(str(exc)) from None


def _cmd_report(project: _Project, args: argparse.Namespace) -> int:
    from operonx.app.evals.report import render

    exp = project.experiment(args.experiment, use_store=not args.no_store)
    _emit(render(exp, args.format), args.out)
    return 0


# ── rescore ──────────────────────────────────────────────────────────────


def _cmd_rescore(project: _Project, args: argparse.Namespace) -> int:
    import asyncio

    from operonx.app.evals import rescore
    from operonx.app.serve.registry import load_object

    exp = project.experiment(args.experiment, use_store=False)
    if exp.path is None:
        raise _Usage(f"{args.experiment}: rescore reads a run's record (its outputs)")
    chosen: Optional[List[Any]] = None
    if args.evaluators:
        if project.app is not None:
            project.app.bootstrap()
        cwd = os.getcwd()
        if cwd not in sys.path:
            sys.path.insert(0, cwd)
        chosen = [load_object(e, field="--evaluators") for e in _csv(args.evaluators)]
    skipped: List[str] = []
    if chosen is None:  # the eval's own, its judges left out (a judge is not rescored)
        from operonx.app.evals.evaluators import _name
        from operonx.app.evals.rescoring import is_judge

        ev = project.eval(exp.eval)
        chosen = [e for e in ev.evaluators if not is_judge(e)]
        skipped = [_name(e) for e in ev.evaluators if is_judge(e)]
    scores = None if args.no_store else project.scores()
    store = project.run_store()
    try:
        got = asyncio.run(rescore(exp.path, chosen, store=store, scores=scores))
    except ValueError as exc:
        raise _Usage(str(exc)) from None
    finally:
        store.close()
    got.skipped = skipped
    s = got.summary
    if args.format == "json":
        _emit(
            json.dumps(
                {
                    "run_id": got.run_id,
                    "summary": s,
                    "verdicts": got.verdicts,
                    "skipped": got.skipped,
                },
                indent=2,
                default=str,
            )
            + "\n",
            None,
        )
        return 0
    rate = s.get("pass_rate")
    print(
        f"rescored {got.run_id} of {got.eval}: passed={s['passed']}/{s['trials']}"
        + (f" ({100 * rate:.1f}%)" if rate is not None else "")
    )
    for name, c in (s.get("checks") or {}).items():
        print(f"  {name}: {c['passed']}/{c['cases']}")
    if got.skipped:
        print(f"  skipped (judges are not rescored): {', '.join(got.skipped)}")
    return 0


# ── calibrate, power ─────────────────────────────────────────────────────


def _gate_tolerance(ev: Any) -> Optional[float]:
    gate = getattr(ev, "gate", None)
    return gate.tolerance_for("pass") if gate is not None else None


def _cmd_calibrate(project: _Project, args: argparse.Namespace) -> int:
    from operonx.app.evals.calibrate import calibrate

    target = _number(args.tolerance, "--tolerance") if args.tolerance else None
    if args.experiments:
        exps = [
            project.experiment(ref, use_store=not args.no_store) for ref in _csv(args.experiments)
        ]
        declared = {e["name"] for e in project.evals()}
        if target is None and exps and exps[0].eval in declared:
            target = _gate_tolerance(project.eval(exps[0].eval))
    else:
        from operonx.app.evals.experiments import load_experiment

        if args.runs < 2:
            raise _Usage("--runs: at least 2 runs of the same code")
        ev = project.eval(args.eval)
        if target is None:
            target = _gate_tolerance(ev)
        if ev.gate is not None and ev.gate.baseline is not None:
            ev.gate = dataclasses.replace(ev.gate, baseline=None)  # A/A: nothing to compare with
        if ev.scores is None and not args.no_store:
            ev.scores = project.scores()
        exps = []
        for i in range(args.runs):
            run = ev.run_sync()
            print(f"run {i + 1}/{args.runs}: {run.summary()}")
            exps.append(load_experiment(run))
    try:
        got = calibrate(exps, target=target, alpha=args.alpha, simulations=args.simulations)
    except ValueError as exc:
        raise _Usage(str(exc)) from None
    print(
        f"{got['eval']}: {got['runs']} runs of {', '.join(got['code_versions']) or 'an unversioned tree'}, "
        f"{got['cases']} cases, {got['repeats']} repeat(s) each"
    )
    print(f"  flaky cases   {got['flaky_share']:.1%}   flip rate {got['flip_rate']:.1%}")
    for name, m in got["metrics"].items():
        sd = "–" if m["sd"] is None else f"{100 * m['sd']:.2f} pts"
        means = ", ".join(f"{100 * x:.1f}%" for x in m["means"])
        print(f"  {name}: {means}  (run-to-run SD {sd})")
    print(
        "  repeats  tolerance (95% of A/A runs pass)" + ("  A/A pass at target" if target else "")
    )
    for row in got["rows"]:
        tail = f"  {row['aa_pass_at_target']:.0%}" if "aa_pass_at_target" in row else ""
        print(f"  {row['repeats']:>7}  {100 * row['tolerance']:>6.1f} pts{tail}")
    if target is not None:
        print(f"  target tolerance {100 * target:.1f} pts")
    r = got["recommended_repeats"]
    if r is not None:
        print(
            f"  recommended: {r} repeat{'s' if r > 1 else ''}, "
            f"tolerance {100 * got['suggested_tolerance']:.1f} pts"
        )
    for note in got["notes"]:
        print(f"  note: {note}")
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "calibration.json").write_text(json.dumps(got, indent=2) + "\n", encoding="utf-8")
        print(f"  written: {out / 'calibration.json'}")
    return 0


def _cmd_align(project: _Project, args: argparse.Namespace) -> int:
    from operonx.app.evals.align import align, describe, record_alignment

    store = project.scores()
    got = align(
        store, args.judge, human=args.human, version=args.version, experiment=args.experiment
    )
    if not got.version:
        raise _Usage(
            f"no scores of judge {args.judge!r} in the score store: run an eval that uses it "
            "(with the store) first"
        )
    if got.n == 0:
        raise _Usage(
            f"judge {args.judge!r} (version {got.version}) and the human scores named "
            f"{got.human!r} share no target: {got.unmatched_judge} judged, "
            f"{got.unmatched_human} labelled elsewhere. Label the runs or items the judge "
            "scored (Studio's review, or Score(source='human') rows), or name the human score "
            "with --human"
        )
    s = got.summary()
    if not args.no_record:
        record_alignment(store, got)
    if args.format == "json":
        _emit(
            json.dumps({**s, "disagreements": got.disagreements}, indent=2, default=str) + "\n",
            None,
        )
    else:
        print(f"{got.judge} @ {got.version} against human {got.human!r}: {describe(s)}")
        c = s["confusion"]
        print(f"  judge PASS: {c['tp']} agree, {c['fp']} people said FAIL")
        print(f"  judge FAIL: {c['tn']} agree, {c['fn']} people said PASS")
        if s["kappa_ci"]:
            lo, hi = s["kappa_ci"]
            print(f"  κ 95% CI [{lo:.2f}, {hi:.2f}] · accuracy {s['accuracy']:.1%}")
        if s["note"]:
            print(f"  note: {s['note']}")
        skipped = {
            k: s[k]
            for k in (
                "unmatched_judge",
                "unmatched_human",
                "human_ties",
                "unusable_human",
                "judge_errors",
            )
            if s[k]
        }
        if skipped:
            print(
                "  not paired: "
                + ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in skipped.items())
            )
        for d in got.disagreements[: args.disagreements]:
            where = d.get("case_id") or d.get("trace_id")
            print(
                f"  disagree {where}: judge {d['judge']}, people {d['human']} — {d['reason'] or ''}"
            )
        verdict = "aligned" if got.aligned else "NOT aligned (κ < 0.6)"
        print(f"  {verdict}" + ("" if args.no_record else "; recorded in the score store"))
    return 0 if got.aligned else 1


def _eval_facts(project: _Project, name: str) -> Tuple[str, int, Path]:
    """An eval's name, case count and record directory — for a declared
    one, without importing the project."""
    from operonx.app.evals import Dataset

    for e in project.evals():
        if e["name"] == name:
            return name, len(Dataset(e["dataset"])), Path(e["record_dir"])
    if ":" in name or project.app is None:
        ev = project.eval(name)
        return ev.name, len(ev.dataset), Path(ev.record_dir)
    have = ", ".join(e["name"] for e in project.evals()) or "none"
    raise _Usage(f"no eval named {name!r} (have: {have})")


def _cmd_power(project: _Project, args: argparse.Namespace) -> int:
    from operonx.app.evals.calibrate import discordance
    from operonx.app.evals.experiments import experiments_of
    from operonx.app.evals.stats import detectable_drop, paired_sample_size

    name, n_now, record_dir = _eval_facts(project, args.eval)
    if args.discordance is not None:
        p_d, how = args.discordance, "given"
    else:
        found = experiments_of(
            name,
            store=None if args.no_store else project.scores(),
            record_dirs=[record_dir],
            limit=2,
        )
        if len(found) < 2:
            raise _Usage(
                f"{args.eval}: power needs the discordance — the share of cases two runs "
                "disagree on: give --discordance, or run the eval twice (before and after a "
                "change) so it can be measured"
            )
        p_d, n_shared = discordance(found[1], found[0])
        how = f"measured on {found[1].experiment_id} → {found[0].experiment_id}, {n_shared} cases"
        if p_d == 0:
            raise _Usage(f"the two newest runs agree on every case ({how}): give --discordance")
    try:
        need = paired_sample_size(p_d, args.delta, alpha=args.alpha, power=args.power)
    except ValueError as exc:
        raise _Usage(str(exc)) from None
    import math

    print(
        f"to detect a {100 * args.delta:.1f}-pt drop with {args.power:.0%} power at "
        f"alpha {args.alpha:g}: {math.ceil(need)} cases "
        f"(discordance {100 * p_d:.1f}% {how})"
    )
    seen = detectable_drop(n_now, p_d, alpha=args.alpha, power=args.power) if n_now else None
    what = (
        f"the smallest drop it detects is {100 * seen:.1f} pts"
        if seen is not None
        else f"it cannot detect even a {100 * p_d:.1f}-pt drop"
    )
    print(f"{name} has {n_now} cases: {what}")
    return 0


# ── list, dataset ────────────────────────────────────────────────────────


def _cmd_list(project: _Project, args: argparse.Namespace) -> int:
    from operonx.app.evals import Dataset
    from operonx.app.evals.fingerprint import dataset_version
    from operonx.app.jobs.record import JobRun, runs_of

    evals = project.evals()
    print(project.app.name if project.app is not None else str(project.root))
    if not evals:
        print("  no evals")
    used = set()
    for e in evals:
        ds = Dataset(e["dataset"])
        used.add(ds.path.resolve())
        rows = ds.rows() if ds.path.is_file() else []
        what = f"{len(rows)} cases {dataset_version(rows)}" if ds.path.is_file() else "missing"
        runs = runs_of(e["record_dir"], e["name"])
        last = "no run yet"
        if runs:
            run = JobRun.load(runs[-1])
            gate = (run.meta.get("eval") or {}).get("gate") or {}
            last = f"last: {gate.get('verdict', run.status)} {run.run_id}"
        repeats = f" x{e['repeats']}" if e["repeats"] > 1 else ""
        print(f"  {e['name']:18s} {ds.name} ({what}){repeats}  {last}")
    others = sorted(
        p for p in (project.root / "datasets").glob("*.jsonl") if p.resolve() not in used
    )
    for p in others:
        rows = Dataset(p).rows()
        print(f"  {'(dataset)':18s} {p.stem} ({len(rows)} cases {dataset_version(rows)})")
    return 0


def _dataset(project: _Project, name: str) -> Any:
    from operonx.app.evals import Dataset, dataset_path

    ref = (
        name
        if (name.endswith(".jsonl") or "/" in name or name.startswith("dataset:"))
        else f"dataset:{name}"
    )
    path = dataset_path(ref, project.root)
    if not path.is_file():
        raise _Usage(f"no dataset at {path}")
    return Dataset(path)


def _cmd_dataset(project: _Project, args: argparse.Namespace) -> int:
    from operonx.app.evals.dataset import diff_rows, parse_rows
    from operonx.app.evals.fingerprint import dataset_version

    ds = _dataset(project, args.name)
    if args.action == "validate":
        problems = ds.problems()
        for line, why in problems:
            print(f"{ds.path}:{line}: {why}")
        if problems:
            return 1
        print(f"{ds.path}: {len(ds.all_rows())} cases, no problems")
        return 0
    if args.action == "stats":
        rows = ds.all_rows()
        splits = Counter(str(r.get("split")) for r in rows if r.get("split") is not None)
        tags = Counter(t for r in rows for t in (r.get("tags") or ()))
        clusters = {r.get("cluster") for r in rows if r.get("cluster") is not None}
        print(f"{ds.path}")
        print(f"  cases     {len(rows)}")
        print(f"  version   {dataset_version(rows)}")
        print(f"  expected  {sum(1 for r in rows if r.get('expected') is not None)}")
        if splits:
            print("  splits    " + ", ".join(f"{k} {v}" for k, v in sorted(splits.items())))
        if tags:
            print("  tags      " + ", ".join(f"{k} {v}" for k, v in tags.most_common()))
        if clusters:
            print(f"  clusters  {len(clusters)}")
        return 0
    # diff against a git ref
    try:
        rel = ds.path.resolve().relative_to(_git_root(ds.path.parent))
    except ValueError:
        raise _Usage(f"{ds.path} is not inside a git checkout") from None
    got = subprocess.run(
        ["git", "show", f"{args.against}:{rel.as_posix()}"],
        cwd=str(ds.path.parent),
        capture_output=True,
        text=True,
    )
    if (
        got.returncode != 0
        and "exists on disk, but not in" not in got.stderr
        and "does not exist" not in got.stderr
    ):
        raise _Usage(f"`git show {args.against}:{rel.as_posix()}`: {got.stderr.strip()}")
    old = (
        parse_rows(got.stdout.splitlines(), where=f"{args.against}:{rel}")
        if got.returncode == 0
        else []
    )
    diff = diff_rows(old, ds.all_rows())
    print(f"{ds.path} against {args.against}")
    for kind in ("added", "removed", "changed"):
        ids = diff[kind]
        print(f"  {kind} {len(ids)}" + (f": {', '.join(ids[:50])}" if ids else ""))
    return 0


def _git_root(where: Path) -> Path:
    got = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], cwd=str(where), capture_output=True, text=True
    )
    if got.returncode != 0:
        raise ValueError("not a git checkout")
    return Path(got.stdout.strip()).resolve()


# ── the parser ───────────────────────────────────────────────────────────


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="operonx eval",
        description="Run experiments, compare and report them, size them.",
        epilog="Exit status (run; compare with --tolerance): 0 pass, 1 failed or regressed, "
        "2 inconclusive under --strict or a command that could not run, 3 an infrastructure error.",
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)

    def command(name: str, help: str) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help, description=help)
        p.add_argument(
            "-f", "--manifest", default=None, help="path to operonx.toml (default: search upward)"
        )
        return p

    run = command("run", "run an eval once: one experiment, exit with the gate's code")
    run.add_argument("eval", help="an eval the project declares, or module:attr")
    run.add_argument(
        "--repeats", type=int, default=None, help="runs per case (default: the eval's)"
    )
    run.add_argument("--split", default=None, help="only the cases of this split")
    run.add_argument(
        "--tag", action="append", default=[], help="only cases with this tag (repeatable: any)"
    )
    run.add_argument("--cases", default=None, metavar="ID,ID", help="only these cases")
    run.add_argument(
        "--sample", type=int, default=None, metavar="N", help="a stable sample of N cases"
    )
    run.add_argument(
        "--baseline",
        default=None,
        help="compare against: latest, a run id, main (= git:origin/main), git:<ref> "
        "(the experiment of the merge-base)",
    )
    run.add_argument(
        "--tolerance",
        action="append",
        default=None,
        metavar="X|METRIC=X",
        help="how large a drop matters (needed with a baseline; repeatable per metric)",
    )
    run.add_argument("--strict", action="store_true", help="inconclusive exits 2")
    run.add_argument("--variant", default=None, help="a label for this experiment")
    run.add_argument("--concurrency", type=int, default=None, help="cases in flight at once")
    run.add_argument(
        "--no-cache", action="store_true", help="ask every judge again (no judge cache)"
    )
    run.add_argument("--report", default=None, metavar="md,json,junit", help="reports to write")
    run.add_argument("--out", default=None, help="where the reports go (default: the run's record)")
    run.add_argument(
        "--no-store", action="store_true", help="do not write the experiment to the score store"
    )
    run.add_argument("--failures", type=int, default=10, help="how many failed keys to print")

    cmp = command("compare", "compare two experiments, paired (the second against the first)")
    cmp.add_argument("a", help="the baseline experiment: a run id or a record directory")
    cmp.add_argument("b", help="the experiment compared with it")
    cmp.add_argument("--tolerance", action="append", default=None, metavar="X|METRIC=X")
    cmp.add_argument("--metrics", default=None, metavar="M,M", help="gated metrics (default: pass)")
    cmp.add_argument("--alpha", type=float, default=0.05)
    cmp.add_argument("--strict", action="store_true")
    cmp.add_argument("--format", choices=("md", "json"), default="md")
    cmp.add_argument("--out", default=None, help="write here instead of stdout")
    cmp.add_argument("--no-store", action="store_true", help="look only in the record directories")
    cmp.add_argument(
        "--pairwise",
        default=None,
        metavar="mod:attr,...",
        help="pairwise judges: which answer is better, case by case, in both orders",
    )

    rep = command("report", "an experiment as Markdown, JSON or JUnit XML")
    rep.add_argument("experiment", help="a run id or a record directory")
    rep.add_argument("--format", choices=("md", "json", "junit"), default="md")
    rep.add_argument("--out", default=None, help="write here instead of stdout")
    rep.add_argument("--no-store", action="store_true", help="look only in the record directories")

    res = command("rescore", "judge a recorded run again, without running its graph")
    res.add_argument("experiment", help="a run id or a record directory (the record is read)")
    res.add_argument(
        "--evaluators", default=None, metavar="mod:attr,...", help="default: the eval's own"
    )
    res.add_argument("--format", choices=("text", "json"), default="text")
    res.add_argument("--no-store", action="store_true", help="do not write the new scores")

    cal = command("calibrate", "run an eval k times on this commit: the noise floor, a tolerance")
    cal.add_argument("eval")
    cal.add_argument("--runs", type=int, default=3)
    cal.add_argument(
        "--experiments", default=None, metavar="ID,ID", help="use these runs instead of running"
    )
    cal.add_argument("--tolerance", default=None, help="the tolerance wanted (default: the gate's)")
    cal.add_argument("--alpha", type=float, default=0.05)
    cal.add_argument("--simulations", type=int, default=200, help="A/A pairs simulated per row")
    cal.add_argument("--out", default=None, help="write calibration.json here")
    cal.add_argument("--no-store", action="store_true")

    pw = command("power", "how many cases a drop needs, and what drop the dataset can see")
    pw.add_argument("eval")
    pw.add_argument("--delta", type=float, required=True, help="the drop to detect (0.05 = 5 pts)")
    pw.add_argument("--alpha", type=float, default=0.05)
    pw.add_argument("--power", type=float, default=0.8)
    pw.add_argument(
        "--discordance",
        type=float,
        default=None,
        help="the share of cases two runs disagree on (default: measured on the newest two)",
    )
    pw.add_argument("--no-store", action="store_true")

    al = command("align", "how often a judge agrees with human labels: κ, TPR, TNR")
    al.add_argument("judge", help="the judge's score name, e.g. judge:polite")
    al.add_argument("--human", default=None, help="the human scores' name (default: the judge's)")
    al.add_argument("--version", default=None, help="the judge version (default: its newest)")
    al.add_argument("--experiment", default=None, help="only this experiment's judge scores")
    al.add_argument("--no-record", action="store_true", help="measure without recording it")
    al.add_argument("--disagreements", type=int, default=10, help="how many to print")
    al.add_argument("--format", choices=("text", "json"), default="text")

    command("list", "the project's evals and datasets, and each eval's last verdict")

    ds = command("dataset", "check a dataset, count it, or diff it against git")
    ds.add_argument("action", choices=("validate", "stats", "diff"))
    ds.add_argument("name", help="a dataset name (datasets/<name>.jsonl) or a path")
    ds.add_argument("--against", default="HEAD", help="diff: the git ref (default: HEAD)")

    on = command("online", "judge a past window of stored runs (a live pass is `operonx run`)")
    on.add_argument("action", choices=("backfill",))
    on.add_argument("name", help="an online eval the project declares")
    on.add_argument("--since", required=True, help="7d, 24h, 30m ago, or epoch seconds")
    on.add_argument("--until", default=None, help="the window's end (default: now)")

    qu = command("queue", "fill a review queue, or list what waits in it")
    qu.add_argument("action", choices=("add", "list"))
    qu.add_argument("queue", help="a [[queue]] the project declares")
    qu.add_argument("--traces", nargs="+", default=None, metavar="ID", help="runs to add")
    qu.add_argument("--experiment", default=None, help="add this experiment's cases")
    qu.add_argument("--failed", action="store_true", help="with --experiment: failed cases only")

    command("migrate-reviews", "write Studio's reviews.jsonl to the score store as human scores")
    return parser


def _since(text: str, now: float) -> float:
    """``7d``, ``24h``, ``30m`` ago, or epoch seconds."""
    text = text.strip()
    units = {"d": 86400, "h": 3600, "m": 60}
    if text[-1:] in units and text[:-1].replace(".", "", 1).isdigit():
        return now - float(text[:-1]) * units[text[-1]]
    try:
        return float(text)
    except ValueError:
        raise _Usage(f"{text!r} is not a duration (7d, 24h, 30m) or epoch seconds") from None


def _cmd_online(project: _Project, args: argparse.Namespace) -> int:
    from operonx.app.evals.online import OnlineEval

    if project.app is None:
        raise _Usage(f"no operonx.toml at or above {Path.cwd()}: online evals are declared there")
    found = project.app.job(args.name)
    if not isinstance(found, OnlineEval):
        raise _Usage(f"{args.name!r} is a {type(found).__name__}, not an online eval")
    now = time.time()
    since = _since(args.since, now)
    until = _since(args.until, now) if args.until else None
    run = asyncio.run(found.backfill(since, until).run())
    online = run.meta.get("online") or {}
    print(
        f"{args.name}: backfill {run.run_id} — {run.counts.get('ok', 0)} runs judged, "
        f"{online.get('unsampled', 0)} not sampled, {online.get('queued', 0)} queued, "
        f"${online.get('spent_usd_today', 0):.4f} spent today"
    )
    return 0 if run.status == "ok" else 1


def _queue_spec(project: _Project, name: str) -> Any:
    specs = {q.name: q for q in (project.app.manifest.queues if project.app else ())}
    if name not in specs:
        known = ", ".join(sorted(specs)) or "none"
        raise _Usage(f"no [[queue]] named {name!r} in operonx.toml (declared: {known})")
    return specs[name]


def _cmd_queue(project: _Project, args: argparse.Namespace) -> int:
    from operonx.app.evals.queues import enqueue, pending

    spec = _queue_spec(project, args.queue)
    queues_dir = project.root / ".operonx" / "queues"
    if args.action == "add":
        added = 0
        for trace_id in args.traces or ():
            enqueue(queues_dir, spec.name, trace_id=trace_id, source="cli")
            added += 1
        if args.experiment:
            from operonx.app.evals import load_experiment

            data = load_experiment(args.experiment, store=project.scores())
            for item in data.items:
                if args.failed and item.get("passed") is not False:
                    continue
                failed = [n for n, c in (item.get("checks") or {}).items() if not c.get("passed")]
                enqueue(
                    queues_dir,
                    spec.name,
                    target="item",
                    experiment_id=data.experiment_id,
                    case_id=str(item.get("case")),
                    trace_id=item.get("trace_id"),
                    source=f"experiment:{data.experiment_id}",
                    reason=", ".join(failed),
                )
                added += 1
        if not added:
            raise _Usage("queue add takes --traces ID … or --experiment ID [--failed]")
        print(f"{spec.name}: {added} added")
        return 0
    left = pending(project.scores(), queues_dir, spec)
    for item, who in left:
        what = item.trace_id or item.session_id or f"{item.experiment_id}/{item.case_id}"
        by = f" (reviewed by {', '.join(who)})" if who else ""
        print(f"{item.target:8} {what}  {item.reason or item.source}{by}")
    print(f"{spec.name}: {len(left)} waiting for {spec.reviewers} reviewer(s)")
    return 0


def _cmd_migrate_reviews(project: _Project, args: argparse.Namespace) -> int:
    from operonx.app.evals.queues import migrate_reviews

    path = project.root / ".operonx" / "reviews.jsonl"
    n = migrate_reviews(path, project.scores())
    print(f"{n} reviews written to the score store from {path}")
    return 0


_COMMANDS = {
    "run": _cmd_run,
    "compare": _cmd_compare,
    "report": _cmd_report,
    "rescore": _cmd_rescore,
    "calibrate": _cmd_calibrate,
    "power": _cmd_power,
    "align": _cmd_align,
    "list": _cmd_list,
    "dataset": _cmd_dataset,
    "online": _cmd_online,
    "queue": _cmd_queue,
    "migrate-reviews": _cmd_migrate_reviews,
}


def main(argv: Optional[Sequence[str]] = None) -> int:
    from operonx.cli.run import _load_dotenv

    args = _parser().parse_args(argv)
    _load_dotenv()
    project: Optional[_Project] = None
    try:
        project = _Project(args.manifest)
        return _COMMANDS[args.command](project, args)
    except (_Usage, ManifestError, ImportError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        if project is not None:
            project.close()


if __name__ == "__main__":
    raise SystemExit(main())
