"""`operonx serve` — run what the manifest declares.

operonx serve                    # every [[serve]] entry
operonx serve --only call        # one of them
operonx serve --list             # what would run, and where
operonx serve --host 0.0.0.0 --port 9000 --reload
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

from operonx.app import Application, ManifestError


def _sinks(d: dict) -> str:
    """Where a service's or job's runs are traced, and which level of the
    precedence chose it — what the operator checks before a deploy."""
    return f"sinks: {', '.join(d['sinks']) or 'none'}  ({d['sinks_from']})"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="operonx serve",
        description="Serve the graphs declared in operonx.toml.",
    )
    parser.add_argument(
        "-f", "--manifest", default=None, help="path to operonx.toml (default: search upward)"
    )
    parser.add_argument(
        "--only", action="append", default=None, help="serve only this [[serve]] name; repeatable"
    )
    parser.add_argument("--list", action="store_true", help="print what would run, and exit")
    parser.add_argument("--host", default=None, help="bind here instead of the declared host")
    parser.add_argument(
        "--port", type=int, default=None, help="bind here instead (one listener only)"
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="restart when a .py, .toml or .yaml file under the project changes (development)",
    )
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
                if s.kind != "asgi":
                    print(f"      {_sinks(d)}")
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

    if args.reload:
        rest = [a for a in (sys.argv[1:] if argv is None else list(argv)) if a != "--reload"]
        return _reload(app.manifest.root, rest)

    try:
        app.serve(only=args.only, host=args.host, port=args.port)
    except ManifestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


#: What a change to restarts the server under ``--reload``.
WATCHED = ("*.py", "*.toml", "*.yaml", "*.yml")


def _snapshot(root: Path) -> Dict[str, float]:
    found: Dict[str, float] = {}
    for pattern in WATCHED:
        for path in root.rglob(pattern):
            parts = set(path.parts)
            if parts & {".venv", ".git", "__pycache__", "node_modules", ".operonx"}:
                continue
            try:
                found[str(path)] = path.stat().st_mtime
            except OSError:
                pass
    return found


def _reload(root: Path, args: List[str], poll: float = 1.0) -> int:
    """Run ``operonx serve <args>`` in a child, and start it again whenever
    a watched file under *root* changes."""
    import signal

    command = [sys.executable, "-c", "from operonx.cli.serve import main; raise SystemExit(main())"]
    child: Optional[subprocess.Popen] = None

    def _stop(signum, frame):  # noqa: ARG001 — a supervisor's stop unwinds to `finally`
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _stop)
    try:
        while True:
            seen = _snapshot(root)
            child = subprocess.Popen([*command, *args], cwd=Path.cwd())
            print(f"[serve --reload] watching {root}", file=sys.stderr)
            while child.poll() is None:
                time.sleep(poll)
                if _snapshot(root) != seen:
                    print("[serve --reload] a file changed: restarting", file=sys.stderr)
                    child.terminate()
                    try:
                        child.wait(10)
                    except subprocess.TimeoutExpired:
                        child.kill()
                    break
            else:
                # it stopped on its own (a crash at startup): wait for a fix
                while _snapshot(root) == seen:
                    time.sleep(poll)
    except KeyboardInterrupt:
        return 0
    finally:
        if child is not None and child.poll() is None:
            child.terminate()
