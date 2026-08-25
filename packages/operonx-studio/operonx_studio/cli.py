"""``operonx-studio`` — render a project's graphs as a browsable page.

    operonx-studio [PATH]              write studio.html for PATH
    operonx-studio [PATH] -o out.html  choose the output file
    operonx-studio [PATH] --open       open it in a browser afterwards
    operonx-studio [PATH] --serve      live-reloading daemon on localhost

Extraction and rendering both run locally: the page is one self-contained
file with no network calls, so it works on a machine with no internet, which
is usually where a workflow needs looking at.
"""

from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path
from typing import Sequence

from operonx_project.extract import ExtractError, extract_project
from operonx_project.manifest import Manifest, ManifestError

from operonx_studio.envstatus import env_status
from operonx_studio.render import render_html

__all__ = ["main"]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="operonx-studio", description=__doc__)
    parser.add_argument("path", nargs="?", default=".", help="project root (default: cwd)")
    parser.add_argument("-o", "--output", default="studio.html", help="output file")
    parser.add_argument("--open", action="store_true", help="open in a browser when done")
    parser.add_argument("--serve", action="store_true", help="run the live-reloading daemon")
    parser.add_argument("--host", default="127.0.0.1", help="serve host (loopback by default)")
    parser.add_argument("--port", type=int, default=8765, help="serve port")
    args = parser.parse_args(argv)

    root = Path(args.path)
    if args.serve:
        if not root.exists():
            print(f"operonx-studio: {root} does not exist", file=sys.stderr)
            return 2
        from operonx_studio.daemon import serve

        if args.open:
            webbrowser.open(f"http://{args.host}:{args.port}/")
        return serve(root, host=args.host, port=args.port)

    try:
        manifest = Manifest.load(root)
        ir = extract_project(manifest)
    except (ManifestError, ExtractError) as exc:
        print(f"operonx-studio: {exc}", file=sys.stderr)
        return 1

    resources = ir.get("resources") or {}
    env = resources.get("env") or {}
    status = env_status(
        manifest.root, env.get("required") or [], (env.get("optional") or {}).keys()
    )

    out = Path(args.output)
    out.write_text(render_html(ir, env_status=status), encoding="utf-8")
    graphs = len(ir["graphs"])
    nodes = sum(len(g["nodes"]) for g in ir["graphs"])
    print(f"operonx-studio: {graphs} graph(s), {nodes} node(s) -> {out}")
    if args.open:
        webbrowser.open(out.resolve().as_uri())
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
