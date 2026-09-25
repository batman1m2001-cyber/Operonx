"""`operonx-run` — run a Job from the command line.

    operonx-run score_calls                   # a [[job]] in operonx.toml, by name
    operonx-run score_calls --resume          # only what the last run did not finish
    operonx-run --list                        # every [[job]], with its schedule
    operonx-run jobs:score_calls              # a Job object, as module:attr
    operonx-run score_calls --show            # what would run, and exit

The command is what a cron entry calls; a job's ``schedule`` declares
when, it does not run anything. Exit status is 0 only when every item
finished cleanly, so a cron mail or a CI step sees a failed batch.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Optional

from operonx.app import Application, ManifestError
from operonx.app.serve.registry import load_object


def _application(path: Optional[str]) -> Application:
    return Application.load(path) if path else Application.find(Path.cwd())


def _list(app: Application) -> int:
    print(app.name)
    jobs = app.describe()["jobs"]
    if not jobs:
        print("  no [[job]] entries")
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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="operonx-run",
        description="Run a Job: one run per item, a record per run.",
    )
    parser.add_argument(
        "job", nargs="?", help="a [[job]] name from operonx.toml, or a Job as `module:attr`"
    )
    parser.add_argument(
        "-f",
        "--manifest",
        default=None,
        help="path to operonx.toml (default: search upward from here)",
    )
    parser.add_argument("--list", action="store_true", help="print the manifest's jobs, and exit")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="skip the keys the job's last run finished; rerun the rest",
    )
    parser.add_argument("--show", action="store_true", help="print what would run, and exit")
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
    args = parser.parse_args(argv)

    from operonx.app.jobs import Job, Runbook

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

    if args.record_dir:
        job.record_dir = Path(args.record_dir)
        if isinstance(job, Runbook):
            for j in job.jobs:
                j.record_dir = Path(args.record_dir)
    if args.concurrency and isinstance(job, Job):
        job.concurrency = args.concurrency

    if args.show:
        print(job.name)
        if isinstance(job, Runbook):
            for line in job.tree().splitlines():
                print(f"  {line}")
            if job.description:
                print(f"  {'description':12s} {job.description}")
        else:
            for k, v in job.describe().items():
                if v not in (None, "", {}):
                    print(f"  {k:12s} {v}")
        print(f"  {'record_dir':12s} {job.record_dir}")
        return 0

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


if __name__ == "__main__":
    raise SystemExit(main())
