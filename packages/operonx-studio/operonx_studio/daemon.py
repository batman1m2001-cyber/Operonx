"""Local daemon: serve a project's graph view and reload it as you edit.

**Extraction runs in a subprocess, never in this process.** Two constraints
force it, and both are load-bearing rather than defensive:

* Extraction imports the project's own modules. Re-extracting after an edit
  in the same interpreter would hand back the *cached* module, so the page
  would show stale structure while claiming to be live — the exact class of
  lie the viewer exists to avoid. A fresh process re-imports from disk.
* ``ResourceHub._instance`` is a class-level singleton and top-level module
  names collide across projects, so one process handles one project.
* The subprocess runs the **project's** interpreter, not ours, so one studio
  installation can serve projects whose dependencies it does not share.

It also means a project that raises on import reports the error in the page
instead of killing the server.

Change detection is mtime polling rather than a filesystem-watch library:
one fewer dependency, works the same over a network mount, and at these
project sizes a scan is cheaper than the extraction it guards.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, Set, Tuple

from operonx_studio.envstatus import env_status
from operonx_studio.render import render_html

__all__ = ["ProjectWatcher", "build_app", "serve"]

# Where a project keeps its interpreter. Extraction imports the project's
# own modules, so it must run under the interpreter that has the project's
# dependencies — callbot needs scipy, which the studio's environment has no
# reason to carry.
VENV_PYTHON = (".venv/bin/python", "venv/bin/python", ".venv/Scripts/python.exe")

WATCH_SUFFIXES = {".py", ".toml", ".yaml", ".yml"}
WATCH_NAMES = {".env", ".env.example"}
SKIP_DIRS = {"__pycache__", ".venv", "venv", ".git", "node_modules", ".ruff_cache", ".pytest_cache"}

_POLL_SECONDS = 0.7

# Injected only when serving. A static file must stay inert.
_LIVE_RELOAD = """
<script>
(function () {
  var since = window.__OPERONX_STAMP__ || 0;
  setInterval(function () {
    fetch('api/stamp').then(function (r) { return r.json(); }).then(function (d) {
      if (since && d.stamp !== since) location.reload();
      since = d.stamp;
    }).catch(function () { /* daemon stopped; keep showing the last good page */ });
  }, 1000);
})();
</script>
"""


@dataclass
class ExtractResult:
    """One extraction attempt — the IR, or why it failed."""

    ir: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    stamp: float = 0.0

    @property
    def ok(self) -> bool:
        return self.ir is not None


@dataclass
class ProjectWatcher:
    """Tracks a project's files and re-extracts when any of them changes."""

    root: Path
    _fingerprint: Tuple = field(default=(), init=False)
    _last: ExtractResult = field(default_factory=ExtractResult, init=False)

    def watched_files(self) -> Set[Path]:
        found: Set[Path] = set()
        for path in self.root.rglob("*"):
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            if path.is_file() and (path.suffix in WATCH_SUFFIXES or path.name in WATCH_NAMES):
                found.add(path)
        return found

    def fingerprint(self) -> Tuple:
        """Sorted (path, mtime, size) — cheap and stable across runs."""
        out = []
        for path in sorted(self.watched_files()):
            try:
                stat = path.stat()
            except OSError:
                continue
            out.append((str(path), stat.st_mtime_ns, stat.st_size))
        return tuple(out)

    def changed(self) -> bool:
        current = self.fingerprint()
        if current != self._fingerprint:
            self._fingerprint = current
            return True
        return False

    def interpreter(self) -> str:
        """The Python that will run extraction.

        A project's own virtualenv if it has one, else ours. This is what
        lets a single studio installation serve projects whose dependencies
        it does not share: the project's interpreter supplies ``operonx`` and
        everything the project imports, while ``operonx_project`` is injected
        through ``PYTHONPATH`` — it is pure Python, so it needs no install
        in the target environment.
        """
        for candidate in VENV_PYTHON:
            path = self.root / candidate
            if path.exists():
                return str(path)
        return sys.executable

    def _child_env(self) -> Dict[str, str]:
        """Environment for the extractor: the toolkit, and nothing else.

        Only the directory containing ``operonx_project`` is added. An
        earlier version injected our whole ``sys.path``, which was harmless
        while the child was our own interpreter and actively destructive
        once it became the project's: another environment's ``site-packages``
        ahead of the child's own path shadows its dependencies and can break
        the import of the standard library itself.

        The path is absolute because the child runs with ``cwd`` set to the
        project, where a relative entry would resolve to the wrong place.
        """
        import operonx_project

        toolkit = str(Path(operonx_project.__file__).resolve().parent.parent)
        env = dict(os.environ)
        # An inherited PYTHONPATH is kept — a user may have set it for this
        # project — but every entry is absolutised and existing-checked
        # first. A relative entry would resolve against the project
        # directory once `cwd` changes and silently import the wrong thing.
        inherited = [
            str(Path(entry).resolve())
            for entry in env.get("PYTHONPATH", "").split(os.pathsep)
            if entry and Path(entry).is_dir()
        ]
        ordered = [toolkit] + [e for e in inherited if e != toolkit]
        env["PYTHONPATH"] = os.pathsep.join(ordered)
        return env

    def extract(self) -> ExtractResult:
        """Run extraction in a fresh interpreter and parse the result."""
        code = (
            "import json,sys;"
            "from operonx_project.manifest import Manifest;"
            "from operonx_project.extract import extract_project;"
            "sys.stdout.write(json.dumps(extract_project(Manifest.load(sys.argv[1]))))"
        )
        proc = subprocess.run(
            [self.interpreter(), "-c", code, str(self.root)],
            capture_output=True,
            text=True,
            cwd=str(self.root),
            env=self._child_env(),
        )
        stamp = time.time()
        if proc.returncode != 0:
            detail = (proc.stderr or "").strip().splitlines()
            self._last = ExtractResult(error="\n".join(detail[-12:]) or "extraction failed", stamp=stamp)
            return self._last
        try:
            self._last = ExtractResult(ir=json.loads(proc.stdout), stamp=stamp)
        except json.JSONDecodeError as exc:
            self._last = ExtractResult(error=f"extractor returned invalid JSON: {exc}", stamp=stamp)
        return self._last

    def refresh(self, force: bool = False) -> ExtractResult:
        if force or self.changed() or self._last.stamp == 0.0:
            return self.extract()
        return self._last

    @property
    def last(self) -> ExtractResult:
        return self._last


def _error_page(root: Path, message: str, stamp: float) -> str:
    """Show the failure in the page rather than a dead server."""
    import html as _html

    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>operonx studio · extraction failed</title>
<style>
body {{ font:14px/1.6 ui-sans-serif,system-ui,sans-serif; margin:0; background:#12151a; color:#e6e9ee; }}
main {{ max-width:860px; margin:56px auto; padding:0 24px; }}
h1 {{ font-size:17px; margin:0 0 6px; }}
p {{ color:#98a2b3; margin:0 0 18px; }}
pre {{ background:#181c23; border:1px solid #272d37; border-radius:9px; padding:16px;
  overflow:auto; font-size:12px; color:#e08163; white-space:pre-wrap; }}
</style></head><body><main>
<h1>Could not build {_html.escape(root.name)}</h1>
<p>The page reloads automatically once the project builds again.</p>
<pre>{_html.escape(message)}</pre>
</main>
<script>window.__OPERONX_STAMP__ = {stamp!r};</script>
{_LIVE_RELOAD}
</body></html>"""


def page_for(watcher: ProjectWatcher, result: ExtractResult) -> str:
    """Render whatever the last extraction produced, success or failure."""
    if not result.ok:
        return _error_page(watcher.root, result.error or "unknown error", result.stamp)
    ir = result.ir or {}
    env = (ir.get("resources") or {}).get("env") or {}
    status = env_status(
        watcher.root, env.get("required") or [], (env.get("optional") or {}).keys()
    )
    page = render_html(ir, env_status=status)
    # Both flags are injected here rather than baked into the template: a
    # file written by `operonx-studio PATH` may be shared or committed, and
    # it must neither poll a daemon that is not there nor offer buttons that
    # call an API it cannot reach.
    flags = (
        f"<script>window.__OPERONX_STAMP__ = {result.stamp!r};"
        f"window.__OPERONX_EDITABLE__ = true;</script>"
    )
    return page.replace("</body>", f"{flags}{_LIVE_RELOAD}</body>")


def build_app(root: Path):
    """FastAPI app serving one project. Import kept local to the ``serve`` extra."""
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse, JSONResponse

    watcher = ProjectWatcher(root=Path(root).resolve())
    app = FastAPI(title="operonx studio", docs_url=None, redoc_url=None)

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        return HTMLResponse(page_for(watcher, watcher.refresh()))

    @app.get("/api/stamp")
    def stamp() -> JSONResponse:
        """Polled by the page; re-extracts only when a watched file moved."""
        return JSONResponse({"stamp": watcher.refresh().stamp, "ok": watcher.last.ok})

    @app.get("/api/ir")
    def ir() -> JSONResponse:
        result = watcher.refresh()
        if not result.ok:
            return JSONResponse({"error": result.error}, status_code=503)
        return JSONResponse(result.ir)

    @app.post("/api/edit")
    def edit(body: Dict[str, Any]) -> JSONResponse:
        """Plan a typed edit, and apply it only when asked.

        ``dry_run`` defaults to **true**: a request that forgets the flag
        previews rather than writes. The response always carries the diff,
        so a UI can show the change before it happens — which is what "code
        is the source of truth" has to mean in practice.

        Applying writes the file; the watcher notices on its next poll and
        the page reloads itself, so there is no separate refresh path to
        keep in step.
        """
        from operonx_project.apply import PlanError, apply_plan, plan_edit
        from operonx_project.manifest import Manifest, ManifestError
        from operonx_project.pyedit import PyEditError

        graph = body.get("graph")
        action = body.get("action")
        if not graph or not action:
            return JSONResponse({"error": "graph and action are required"}, status_code=400)
        arguments = {
            k: v for k, v in body.items() if k not in {"graph", "action", "dry_run"}
        }
        try:
            manifest = Manifest.load(watcher.root)
            plan = plan_edit(manifest, graph, action, **arguments)
        except (PlanError, ManifestError, PyEditError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except TypeError as exc:  # wrong arguments for the action
            return JSONResponse({"error": f"bad arguments for {action!r}: {exc}"}, status_code=400)

        payload = {
            "graph": plan.graph,
            "action": plan.action,
            "file": str(plan.file.relative_to(watcher.root)),
            "changed": plan.changed,
            "diff": plan.diff,
            "applied": False,
        }
        if body.get("dry_run", True):
            return JSONResponse(payload)
        try:
            payload["applied"] = apply_plan(plan)
        except PlanError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        return JSONResponse(payload)

    return app


def _watch_forever(watcher: ProjectWatcher) -> Iterator[None]:  # pragma: no cover
    while True:
        watcher.refresh()
        time.sleep(_POLL_SECONDS)
        yield


def serve(root: Path, host: str = "127.0.0.1", port: int = 8765) -> int:  # pragma: no cover
    """Run the daemon. Binds loopback only — this reads local source."""
    try:
        import uvicorn
    except ModuleNotFoundError:
        print(
            "operonx-studio serve needs the web stack:\n"
            "  pip install operonx-studio[serve]",
            file=sys.stderr,
        )
        return 1
    print(f"operonx-studio: serving {root} at http://{host}:{port}")
    uvicorn.run(build_app(root), host=host, port=port, log_level="warning")
    return 0
