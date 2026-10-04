"""``operonx-agents`` — the command line.

operonx-agents probe inhouse qwen3.7-plus --resources resources.yaml --env .env
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import List, Optional

__all__ = ["main"]


def _probe(args: argparse.Namespace) -> int:
    import operonx

    from operonx_agents.probe import probe

    if args.env:
        from dotenv import load_dotenv

        load_dotenv(args.env, override=False)
    operonx.bootstrap(resources=args.resources, env=not args.env)
    reports = []
    for resource in args.resource:
        report = asyncio.run(probe(resource))
        reports.append(report)
        if not args.json:
            print(f"llm:{resource} ({report.model})")
            print(f"  json_schema   {report.json_schema}")
            print(f"  forced tool   {report.forced_tool}")
            print(f"  logprobs      {report.logprobs}")
            if report.declare:
                print(f"  declare       structured_output: {report.declare}")
            else:
                print("  declare       (endpoint not up; nothing to declare)")
    if args.json:
        print(json.dumps([r.to_dict() for r in reports], indent=1, ensure_ascii=False))
    return 0 if all(r.declare for r in reports) else 1


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="operonx-agents")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser(
        "probe",
        help="measure an llm: resource's structured-output support and print what to declare",
    )
    p.add_argument("resource", nargs="+", help="resources.yaml keys, without 'llm:'")
    p.add_argument("--resources", default="resources.yaml", help="path to resources.yaml")
    p.add_argument("--env", default=None, help="a .env to load first (default: ./.env)")
    p.add_argument("--json", action="store_true", help="print the raw report as JSON")
    p.set_defaults(run=_probe)
    args = parser.parse_args(argv)
    return args.run(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
