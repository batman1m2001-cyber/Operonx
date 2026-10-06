"""`operonx run` — run a Job from the command line.

    operonx run score_calls                   # a job the application declares, by name
    operonx run score_calls --resume          # only what the last run did not finish
    operonx run --list                        # every job
    operonx run jobs:score_calls              # a Job object, as module:attr
    operonx run score_calls --show            # what would run, and exit
    operonx run score_calls --set day=2026-09-25 --items data/today.jsonl

A Job is also its own command line — ``job.main()`` takes the same
flags, minus the name, so ``python -m jobs.score_calls --resume`` works
from a package whose ``__main__.py`` calls it.

This is what cron, a systemd timer or a CI schedule calls. Exit status:
0 when the run is ``ok``, 1 when an item (or a step) failed, 2 when the
job could not start (a bad flag, a name it does not know). An eval exits
with its gate's code: 0 pass, 1 failed or regressed, 2 inconclusive
under ``Gate(strict=True)``, 3 an infrastructure error.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, List, Optional, Sequence

from operonx.app import Application, ManifestError
from operonx.app.serve.registry import load_object
from operonx.cli.serve import _sinks


def _application(path: Optional[str]) -> Application:
    return Application.load(path) if path else Application.find(Path.cwd())


def _list(app: Application) -> int:
    print(app.name)
    jobs = app.describe()["jobs"]
    if not jobs:
        print("  no jobs")
        return 0
    for j in jobs:
        if j["kind"] == "steps":
            print(f"  {j['name']:18s} {'steps':6s} {' -> '.join(j['steps'])}")
        else:
            reduce = f" -> reduce {j['reduce']}" if j.get("reduce") else ""
            print(f"  {j['name']:18s} {j['kind']:6s} {j['items'] or '-'} -> {j['graph']}{reduce}")
        if j["description"]:
            print(f"  {'':18s} {j['description']}")
        print(f"  {'':18s} {_sinks(j)}")
    return 0


def _value(text: str) -> Any:
    """``--set k=v``: JSON when it parses (numbers, true, lists), else text."""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return text


def _add_run_flags(parser: argparse.ArgumentParser) -> None:
    """The flags every way of running a job shares."""
    parser.add_argument(
        "--resume",
        action="store_true",
        help="skip the keys the job's last run finished; rerun the rest",
    )
    parser.add_argument("--show", action="store_true", help="print what would run, and exit")
    parser.add_argument(
        "--set",
        dest="sets",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="an input for the graph (repeatable); VALUE is JSON when it parses",
    )
    parser.add_argument("--items", default=None, help="read items from this .jsonl file instead")
    parser.add_argument(
        "--record-dir",
        default=None,
        help="where to write the run record (default: the project's .operonx/jobs)",
    )
    parser.add_argument(
        "--concurrency", type=int, default=None, help="items in flight at once (default: the job's)"
    )
    parser.add_argument(
        "--failures", type=int, default=10, help="how many failed keys to print (default: 10)"
    )


def _apply(job: Any, args: argparse.Namespace) -> None:
    """Command-line overrides onto a Job, or onto every step of a job of steps."""
    from operonx.app.jobs.items import check_items

    sets = {}
    for pair in args.sets:
        key, sep, value = pair.partition("=")
        if not sep or not key:
            raise ValueError(f"--set wants KEY=VALUE, not {pair!r}")
        sets[key.strip()] = _value(value)

    def each(j: Any) -> List[Any]:
        return [j] if j.steps is None else [m for s in j.steps for m in each(s)]

    if job.steps is not None and (args.items or args.concurrency):
        raise ValueError("--items / --concurrency name one job's settings; run that step instead")
    for j in each(job):
        j.inputs.update(sets)
    if args.items:
        check_items(args.items, job.name)
        job.items = args.items
    if args.concurrency:
        job.concurrency = args.concurrency


def _show(job: Any) -> int:
    print(job.name)
    for k, v in job.describe().items():
        if v not in (None, "", {}, []):
            print(f"  {k:12s} {v}")
    if job.steps is None and job.inputs:
        print(f"  {'inputs':12s} {job.inputs}")
    print(f"  {'records':12s} {job.records()}")
    return 0


def _run(job: Any, args: argparse.Namespace) -> int:
    """Run *job*, print its outcome, return the exit status."""
    try:
        _apply(job, args)
    except (ValueError, TypeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.record_dir:
        job.record_dir = Path(args.record_dir)
    if args.show:
        return _show(job)

    run = job.run_sync(resume=args.resume, record_dir=args.record_dir)
    print(run.summary())
    print(f"  {run.path}")
    if job.steps is not None:
        for step in run.meta.get("steps") or []:
            if step["status"] != "ok":
                print(f"  {step['status']} {step['name']}  {step.get('path', '')}".rstrip())
        return 0 if run.status == "ok" else 1
    return outcome(run, args.failures)


def outcome(run: Any, failures: int = 10) -> int:
    """Print what a finished job run left beyond its summary line — the
    items that failed, the run's error, an eval's gate reasons — and
    return its exit status: an eval's gate code, else 0 only when the
    run is ``ok``."""
    bad = [i for i in run.items if i.status in ("failed", "timeout")]
    for item in bad[:failures]:
        print(f"  {item.status} {item.key}: {item.error}")
    if len(bad) > failures:
        print(f"  … and {len(bad) - failures} more, in {run.path / 'items.jsonl'}")
    if run.meta.get("error"):
        print(f"  {run.meta['error']}")
    gate = (run.meta.get("eval") or {}).get("gate")
    if gate:  # an eval: its gate chose the exit status (0 pass, 1 failed, 2, 3)
        for line in gate.get("reasons") or ():
            print(f"  gate: {line}")
        for line in gate.get("warnings") or ():
            print(f"  warning: {line}")
        return int(gate["exit_code"])
    return 0 if run.status == "ok" else 1


def main_for(job: Any, argv: Optional[Sequence[str]] = None, *, doc: Optional[str] = None) -> int:
    """*job* as a command line. What ``job.main()`` calls."""
    parser = argparse.ArgumentParser(
        prog=f"{job.name}",
        description=doc or getattr(job, "description", "") or None,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_run_flags(parser)
    return _run(job, parser.parse_args(argv))


def _load_dotenv() -> None:
    """``.env`` before the manifest: ``${VAR}`` in operonx.toml is resolved
    when the file is parsed, so a value that lives only in ``.env`` would
    otherwise read as unset."""
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - dotenv is a dependency
        return
    env = Path.cwd() / ".env"
    if env.is_file():
        load_dotenv(env, override=False)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="operonx run",
        description="Run a Job: one run per item, a record per run.",
    )
    parser.add_argument(
        "job", nargs="?", help="a job the application declares, or a Job as `module:attr`"
    )
    parser.add_argument(
        "-f",
        "--manifest",
        default=None,
        help="path to operonx.toml (default: search upward from here)",
    )
    parser.add_argument(
        "--list", action="store_true", help="print the application's jobs, and exit"
    )
    _add_run_flags(parser)
    args = parser.parse_args(argv)

    from operonx.app.jobs import Job

    _load_dotenv()
    try:
        if args.list:
            return _list(_application(args.manifest))
        if not args.job:
            parser.error("name a job, or --list")
        if ":" in args.job:
            # `module:attr` is relative to the project, the way `operonx serve`
            # resolves a manifest's entry points.
            cwd = os.getcwd()
            if cwd not in sys.path:
                sys.path.insert(0, cwd)
            job = load_object(args.job, field="job")
            if not isinstance(job, Job):
                print(f"error: {args.job!r} is a {type(job).__name__}, not a Job", file=sys.stderr)
                return 2
        else:
            job = _application(args.manifest).job(args.job)
    except (ManifestError, ImportError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return _run(job, args)


if __name__ == "__main__":
    raise SystemExit(main())
