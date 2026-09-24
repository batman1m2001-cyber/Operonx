"""`operonx-run` — run a Job from the command line.

    operonx-run jobs:score_calls              # a Job, as module:attr
    operonx-run jobs:score_calls --resume     # only what the last run did not finish
    operonx-run jobs:score_calls --show       # what would run, and exit

The command is what a cron entry calls; the job's own ``schedule`` is a
declaration of when, not a scheduler. Exit status is 0 only when every
item finished cleanly, so a cron mail or a CI step sees a failed batch.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from operonx.core.serve.registry import load_object


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="operonx-run",
        description="Run a Job: one run per item, a record per run.",
    )
    parser.add_argument("job", help="the Job, as `module:attr`, imported from the current directory")
    parser.add_argument("--resume", action="store_true",
                        help="skip the keys the job's last run finished; rerun the rest")
    parser.add_argument("--show", action="store_true",
                        help="print what would run, and exit")
    parser.add_argument("--record-dir", default=None,
                        help="where to write the run record (default: the job's record_dir)")
    parser.add_argument("--concurrency", type=int, default=None,
                        help="items in flight at once (default: the job's)")
    parser.add_argument("--failures", type=int, default=10,
                        help="how many failed keys to print (default: 10)")
    args = parser.parse_args(argv)

    # `module:attr` is relative to the project, the way `operonx-serve`
    # resolves a manifest's entry points.
    cwd = os.getcwd()
    if cwd not in sys.path:
        sys.path.insert(0, cwd)

    from operonx.core.jobs import Job

    try:
        job = load_object(args.job, field="job")
    except (ImportError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not isinstance(job, Job):
        print(f"error: {args.job!r} is a {type(job).__name__}, not a Job", file=sys.stderr)
        return 2

    if args.record_dir:
        job.record_dir = args.record_dir
    if args.concurrency:
        job.concurrency = args.concurrency

    if args.show:
        print(job.name)
        for k, v in job.describe().items():
            if v not in (None, "", {}):
                print(f"  {k:12s} {v}")
        print(f"  {'record_dir':12s} {job.record_dir}")
        return 0

    run = job.run_sync(resume=args.resume)
    print(run.summary())
    print(f"  {run.path}")
    failed = run.failed
    for item in failed[: args.failures]:
        print(f"  failed {item.key}: {item.error}")
    if len(failed) > args.failures:
        print(f"  … and {len(failed) - args.failures} more, in {run.path / 'items.jsonl'}")
    if run.meta.get("error"):
        print(f"  {run.meta['error']}")
    return 0 if run.status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
