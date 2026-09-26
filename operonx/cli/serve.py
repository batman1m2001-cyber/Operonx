"""`operonx serve` — run what the manifest declares.

operonx-serve                    # every [[serve]] entry
operonx-serve --only call        # one of them
operonx-serve --list             # what would run, and where
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from operonx.app import Application, ManifestError


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="operonx-serve",
        description="Serve the graphs declared in operonx.toml.",
    )
    parser.add_argument(
        "-f", "--manifest", default=None, help="path to operonx.toml (default: search upward)"
    )
    parser.add_argument(
        "--only", action="append", default=None, help="serve only this [[serve]] name; repeatable"
    )
    parser.add_argument("--list", action="store_true", help="print what would run, and exit")
    args = parser.parse_args(argv)

    try:
        app = Application.load(args.manifest) if args.manifest else Application.find(Path.cwd())
    except ManifestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.list:
        print(app.name)
        if not app.services:
            print("  no [[serve]] entries")
        from operonx.app.declare import ref_name

        described = {d["name"]: d for d in app.describe()["services"]}
        for (host, port), specs in app.manifest.listeners().items():
            workers = f"  x{specs[0].workers} workers" if specs[0].workers > 1 else ""
            print(f"  {host}:{port}{workers}")
            for s in specs:
                d = described[s.name]
                target = d["app"] if s.kind == "asgi" else d["graph"]
                bound = f" max_inflight={s.max_inflight}" if s.max_inflight else ""
                print(
                    f"    {s.name:14s} {s.kind:10s} {s.path:16s} -> {target}  [{s.session}{bound}]"
                )
                if d["on_startup"]:
                    print(f"      on_startup={','.join(d['on_startup'])}")
                if d["on_session"] or d["on_close"]:
                    print(
                        f"      on_session={d['on_session'] or '-'} on_close={d['on_close'] or '-'}"
                    )
                for v, bind in s.variants.items():
                    print(
                        f"      [{v}]" + "".join(f" {k}={ref_name(val)}" for k, val in bind.items())
                    )
        return 0

    try:
        app.serve(only=args.only)
    except ManifestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
