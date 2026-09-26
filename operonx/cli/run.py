"""`operonx-run` — run a Job from the command line.

    operonx-run score_calls                   # a job the application declares, by name
    operonx-run score_calls --resume          # only what the last run did not finish
    operonx-run --list                        # every job, with its schedule
    operonx-run jobs:score_calls              # a Job object, as module:attr
    operonx-run score_calls --show            # what would run, and exit
    operonx-run score_calls --set day=2026-09-25 --sink out/today.jsonl

A Job or Runbook is also its own command line — ``job.main()`` takes the
same flags, minus the name, so ``python -m jobs.score_calls --resume``
works from a package whose ``__main__.py`` calls it.

The command is what a cron entry calls; a job's ``schedule`` declares
when, it does not run anything. Exit status is 0 only when every item
finished cleanly, so a cron mail or a CI step sees a failed batch.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Optional, Sequence

from operonx.app import Application, ManifestError
from operonx.app.serve.registry import load_object


def _application(path: Optional[str]) -> Application:
    return Application.load(path) if path else Application.find(Path.cwd())


def _list(app: Application) -> int:
    print(app.name)
    jobs = app.describe()["jobs"]
    if not jobs:
        print("  no jobs")
        return 0
    for j in jobs:
        when = f"  [{j['schedule']}]" if j["schedule"] else ""
        if j["kind"] == "runbook":
            print(f"  {j['name']:18s} {'runbook':9s} {j['runbook']:30s}{when}")
        else:
            io = f"{j['source'] or '-'} -> {j['sink'] or '-'}"
            print(f"  {j['name']:18s} {j['session']:9s} {j['graph']:30s} {io}{when}")
        if j["description"]:
            print(f"  {'':18s} {j['description']}")
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
    parser.add_argument("--source", default=None, help="read items from here instead")
    parser.add_argument("--sink", default=None, help="write results here instead")
    parser.add_argument(
        "--record-dir",
        default=None,
        help="where to write the run record (default: the job's record_dir)",
    )
    parser.add_argument(
        "--concurrency", type=int, default=None, help="items in flight at once (default: the job's)"
    )
    parser.add_argument(
        "--failures", type=int, default=10, help="how many failed keys to print (default: 10)"
    )


def _apply(job: Any, args: argparse.Namespace) -> None:
    """Command-line overrides onto a Job, or onto every job of a Runbook."""
    from operonx.app.jobs import Runbook

    sets = {}
    for pair in args.sets:
        key, sep, value = pair.partition("=")
        if not sep or not key:
            raise ValueError(f"--set wants KEY=VALUE, not {pair!r}")
        sets[key.strip()] = _value(value)

    jobs = job.jobs if isinstance(job, Runbook) else [job]
    if isinstance(job, Runbook) and (args.source or args.sink):
        raise ValueError("--source / --sink name one job's data; run that job instead")
    for j in jobs:
        j.inputs.update(sets)
        if args.record_dir:
            j.record_dir = Path(args.record_dir)
    if args.record_dir:
        job.record_dir = Path(args.record_dir)
    if not isinstance(job, Runbook):
        if args.source:
            job.source = args.source
        if args.sink:
            job.sink = args.sink
        if args.concurrency:
            job.concurrency = args.concurrency


def _show(job: Any) -> int:
    from operonx.app.jobs import Runbook

    print(job.name)
    if isinstance(job, Runbook):
        for line in job.tree().splitlines():
            print(f"  {line}")
        print(f"  {'jobs':12s} " + ", ".join(f"{j.name} ({j.session})" for j in job.jobs))
        if job.schedule:
            print(f"  {'schedule':12s} {job.schedule}")
        if job.description:
            print(f"  {'description':12s} {job.description}")
    else:
        for k, v in job.describe().items():
            if v not in (None, "", {}, []):
                print(f"  {k:12s} {v}")
        if job.inputs:
            print(f"  {'inputs':12s} {job.inputs}")
    print(f"  {'record_dir':12s} {job.record_dir}")
    return 0


def _run(job: Any, args: argparse.Namespace) -> int:
    """Run *job* (a Job or Runbook), print its outcome, return the exit status."""
    from operonx.app.jobs import Runbook

    try:
        _apply(job, args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.show:
        return _show(job)

    try:
        run = job.run_sync(resume=args.resume)
    except ValueError as exc:  # a stream job asked to resume
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(run.summary())
    print(f"  {run.path}")
    if isinstance(job, Runbook):
        for node in run.jobs:
            if node.status != "ok":
                print(f"  {node.status} {node.name}: {node.error or ''}".rstrip())
        return 0 if run.status == "ok" else 1

    bad = [i for i in run.items if i.status in ("failed", "timeout")]
    for item in bad[: args.failures]:
        print(f"  {item.status} {item.key}: {item.error}")
    if len(bad) > args.failures:
        print(f"  … and {len(bad) - args.failures} more, in {run.path / 'items.jsonl'}")
    if run.meta.get("error"):
        print(f"  {run.meta['error']}")
    return 0 if run.status == "ok" else 1


def main_for(job: Any, argv: Optional[Sequence[str]] = None, *, doc: Optional[str] = None) -> int:
    """*job* (a Job or Runbook) as a command line. What ``job.main()`` calls."""
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
        prog="operonx-run",
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

    from operonx.app.jobs import Job, Runbook

    _load_dotenv()
    try:
        if args.list:
            return _list(_application(args.manifest))
        if not args.job:
            parser.error("name a job, or --list")
        if ":" in args.job:
            # `module:attr` is relative to the project, the way `operonx-serve`
            # resolves a manifest's entry points.
            cwd = os.getcwd()
            if cwd not in sys.path:
                sys.path.insert(0, cwd)
            job = load_object(args.job, field="job")
            if not isinstance(job, (Job, Runbook)):
                print(
                    f"error: {args.job!r} is a {type(job).__name__}, not a Job or a Runbook",
                    file=sys.stderr,
                )
                return 2
        else:
            job = _application(args.manifest).job(args.job)
    except (ManifestError, ImportError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return _run(job, args)


if __name__ == "__main__":
    raise SystemExit(main())
