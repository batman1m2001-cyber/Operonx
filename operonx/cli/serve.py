"""`operonx serve` — run what the manifest declares.

    operonx-serve                    # every [[serve]] entry
    operonx-serve --only call        # one of them
    operonx-serve --list             # what would run, and where
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from operonx.core.manifest import Manifest, ManifestError


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="operonx-serve",
        description="Serve the graphs declared in operonx.toml.",
    )
    parser.add_argument("-f", "--manifest", default=None,
                        help="path to operonx.toml (default: search upward)")
    parser.add_argument("--only", action="append", default=None,
                        help="serve only this [[serve]] name; repeatable")
    parser.add_argument("--list", action="store_true",
                        help="print what would run, and exit")
    args = parser.parse_args(argv)

    try:
        manifest = (Manifest.from_file(args.manifest) if args.manifest
                    else Manifest.find(Path.cwd()))
    except ManifestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.list:
        print(manifest.name)
        if not manifest.serves:
            print("  no [[serve]] entries")
        for (host, port), specs in manifest.listeners().items():
            print(f"  {host}:{port}")
            for s in specs:
                target = s.app if s.kind == "asgi" else s.graph
                bound = f" max_inflight={s.max_inflight}" if s.max_inflight else ""
                print(f"    {s.name:14s} {s.kind:10s} {s.path:16s} -> {target}"
                      f"  [{s.session}{bound}]")
        return 0

    from operonx.core.serve.app import serve_manifest

    try:
        serve_manifest(manifest, only=args.only)
    except ManifestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
