"""``operonx check``: one command that says whether the project works.

    operonx check [DIR]          lint, the tests, every graph built, every route reached
    operonx check --call         also call each POST/PUT/PATCH route once, models faked
    operonx check --no-tests     skip pytest (the rest takes a few seconds)

One line per check, then a verdict; it exits 1 when a check fails. It runs
in this process and binds no port, so run it in the project's environment
(``uv run operonx check``).

* **lint**: ``operonx lint`` (its baseline held), errors only.
* **tests**: ``pytest -q -x`` when ``tests/`` holds a test.
* **build**: every graph the application names, built with the network
  blocked (``operonx lint --build``): an import error, a resource key that
  ``resources.yaml`` lacks, a provider whose extra is not installed.
* **routes**: every route of every listener is resolved through the
  listener's own router, so a request that another app answers (a full path
  under an ``asgi(path)`` prefix, a route listed after the ``/`` mount) fails
  with the name of what answers it. ``GET`` routes are called. Others are
  only resolved, because calling them writes what they write; ``--call``
  calls them once with placeholder values and every ``llm:`` resource
  swapped for ``api_type: fake``, so it costs nothing and needs no key.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

__all__ = ["Line", "main", "run_check"]

#: methods a route may be called with by default: they change nothing
SAFE = ("GET",)
#: methods ``--call`` calls; DELETE never
CALLED = ("POST", "PUT", "PATCH")
#: shown in full up to this many route lines; past it only the ones not ok
ROUTE_LINES = 10
PREFIX_HINT = (
    "asgi(path) mounts a prefix: it answers /x/, not /x. Make the route a Service with "
    "http(...) (AGENTS.md, Recipes: A route)"
)
ROOT_HINT = "a mount at / answers every path: list it last in APP"


def _hint(leaf: Any) -> str:
    from starlette.routing import Mount

    return ROOT_HINT if isinstance(leaf, Mount) and leaf.path == "" else PREFIX_HINT


@dataclass
class Line:
    """One printed check: ``ok``, ``FAIL`` or ``--`` (skipped)."""

    state: str
    what: str
    detail: str = ""

    def __str__(self) -> str:
        return f"  {self.state:5} {self.what:22} {self.detail}".rstrip()


def _first(text: str) -> str:
    line = next((x for x in str(text).splitlines() if x.strip()), "")
    return line if len(line) <= 160 else line[:157] + "..."


def _error(exc: BaseException) -> str:
    return _first(f"{type(exc).__name__}: {exc}")


# -- lint -------------------------------------------------------------------


def _lint(root: Path) -> Line:
    from operonx.cli.lint import _baseline, _hold, _manifest
    from operonx.lint.rules import lint_path

    problems = _manifest(root)
    if problems:
        return Line("FAIL", "lint", problems[0])
    findings, _ = _hold(lint_path(root), _baseline(root))
    errors = [f for f in findings if f.severity == "error"]
    warnings = len(findings) - len(errors)
    if not errors:
        return Line("ok", "lint", "clean" + (f" ({warnings} warning(s))" if warnings else ""))
    f = errors[0]
    try:
        where = f.file.resolve().relative_to(root)
    except ValueError:
        where = f.file
    more = f"; `operonx lint` for all {len(errors)}" if len(errors) > 1 else ""
    return Line("FAIL", "lint", f"{where}:{f.line}: [{f.rule}] {f.message}{more}")


# -- tests ------------------------------------------------------------------

_SUMMARY = re.compile(r"\d+ (passed|failed|error|errors|skipped)\b")


def _tests(root: Path) -> Line:
    tests = root / "tests"
    if not tests.is_dir() or not any(tests.rglob("test_*.py")):
        return Line("--", "tests", "no tests/test_*.py")
    try:
        done = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", str(tests)],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=900,
        )
    except subprocess.TimeoutExpired:
        return Line("FAIL", "tests", "pytest ran over 15 minutes")
    lines = done.stdout.splitlines()
    summary = next((x.strip(" =") for x in reversed(lines) if _SUMMARY.search(x)), "")
    if done.returncode == 0:
        return Line("ok", "tests", summary)
    if "No module named pytest" in done.stderr:
        return Line("--", "tests", "pytest is not installed in this environment")
    failed = next((x for x in lines if x.startswith(("FAILED ", "ERROR "))), "")
    return Line("FAIL", "tests", _first(failed or summary or done.stderr or "pytest failed"))


# -- build ------------------------------------------------------------------


def _build(root: Path) -> Tuple[Line, bool]:
    """The build line, and whether the application loaded at all."""
    from operonx.lint.build import check_build

    reports = check_build(root)
    if len(reports) == 1 and reports[0].graph == "application" and not reports[0].ok:
        return Line("FAIL", "build", f"the application: {_first(reports[0].error or '')}"), False
    bad = [r for r in reports if not r.clean]
    if not bad:
        return Line("ok", "build", f"{len(reports)} graph(s) build offline"), True
    r = bad[0]
    why = f"reached the network: {r.network[0]}" if r.network else _first(r.error or "")
    more = f" (+{len(bad) - 1} more)" if len(bad) > 1 else ""
    return Line("FAIL", "build", f"graph {r.graph}: {why}{more}"), True


# -- routes -----------------------------------------------------------------


@dataclass
class _Target:
    method: str
    path: str
    leaf: Any  # the Route, or the Mount of an app with no routes (static files)
    label: str


#: (method, path) -> the service an http door or webhook route belongs to
_SERVICES: Dict[Tuple[str, str], str] = {}


def _label(leaf: Any) -> str:
    from starlette.routing import Mount

    if isinstance(leaf, Mount):
        return f"{type(leaf.app).__name__} at {leaf.path or '/'}"
    for method in sorted(getattr(leaf, "methods", None) or ()):
        if (method, leaf.path) in _SERVICES:
            return f"service {_SERVICES[method, leaf.path]}"
    endpoint = getattr(leaf, "endpoint", None)
    name = getattr(endpoint, "__qualname__", None) or getattr(endpoint, "__name__", None)
    if name and "<locals>" not in name:
        return name
    return f"route {leaf.path}"


def _targets(routes: Sequence[Any], prefix: str = "") -> Iterator[_Target]:
    from starlette.routing import Mount, Route

    for r in routes:
        if isinstance(r, Mount):
            inner = getattr(r.app, "routes", None)
            if inner is not None:
                yield from _targets(inner, prefix + r.path)
            else:
                yield _Target("GET", (prefix + r.path) + "/", r, _label(r))
        elif isinstance(r, Route):
            if r.path == "/healthz" and not prefix:
                continue  # operonx's own
            methods = sorted(r.methods or {"GET"})
            method = "GET" if "GET" in methods else next(iter(methods), "GET")
            yield _Target(method, prefix + r.path, r, _label(r))


def _resolve(routes: Sequence[Any], scope: Dict[str, Any]) -> Tuple[Any, str]:
    """What the router does with *scope*: the leaf that answers it and how
    (``full``), the route whose path matched but not its method (``partial``,
    a 405), or nothing (a 404)."""
    from starlette.routing import Match, Mount

    partial = None
    for r in routes:
        match, child = r.matches(scope)
        if match == Match.FULL:
            inner = getattr(r.app, "routes", None) if isinstance(r, Mount) else None
            if inner is not None:
                return _resolve(inner, {**scope, **child})
            return r, "full"
        if match == Match.PARTIAL and partial is None:
            partial = r
    return (partial, "partial") if partial is not None else (None, "none")


_PARAM = re.compile(r"\{(\w+)(?::(\w+))?\}")


def _concrete(path: str) -> str:
    return _PARAM.sub(lambda m: "1" if m[2] in ("int", "float") else "check", path) or "/"


def _fake_models() -> None:
    """Every ``llm:`` resource answers from a script, in this process only."""
    from operonx.core.registry.resource_hub import ResourceHub
    from operonx.providers.llms.config import FakeConfig

    hub = ResourceHub._instance
    if hub is None:
        return
    for key in hub.keys():
        if key.startswith("llm:"):
            fake = FakeConfig.model_validate({"api_type": "fake", "script": [{"echo": "check: "}]})
            hub.register(fake, key, persist=False)


def _body_for(client: Any, t: _Target, path: str) -> Tuple[Any, Dict[str, Any]]:
    """Call *t* with ``{}``; while the door names a missing parameter, add
    it as ``"check"`` and call again."""
    body: Dict[str, Any] = {}
    for _ in range(12):
        resp = client.request(t.method, path, json=body)
        if resp.status_code != 400:
            return resp, body
        try:
            msg = resp.json()
        except ValueError:
            return resp, body
        field = msg.get("field") if isinstance(msg, dict) else None
        if not field or "missing" not in str(msg.get("error", "")) or field in body:
            return resp, body
        body[field] = "check"
    return resp, body


def _probe(client: Any, routes: Sequence[Any], t: _Target, call: bool) -> Line:
    shown = f"{t.method:4} {t.path}"
    path = _concrete(t.path)
    scope = {"type": "http", "path": path, "root_path": "", "method": t.method, "headers": []}
    leaf, how = _resolve(routes, scope)
    if leaf is not t.leaf:
        if how == "none":
            return Line("FAIL", shown, f"404: nothing answers it. {PREFIX_HINT}")
        status = "405" if how == "partial" else "answered"
        return Line("FAIL", shown, f"{status} by {_label(leaf)}, not {t.label}. {_hint(leaf)}")
    bare = _slash_twin(routes, t, path)
    if bare:
        return Line("FAIL", shown, bare)
    has_params = path != t.path
    if t.method not in SAFE and not (call and t.method in CALLED):
        why = "never called" if t.method not in CALLED else "--call calls it"
        return Line("ok", shown, f"reaches {t.label} ({why})")
    try:
        if t.method in SAFE:
            resp, sent = client.request(t.method, path), None
        else:
            resp, sent = _body_for(client, t, path)
    except Exception as exc:  # the app raised: what a 500 hides
        return Line("FAIL", shown, f"500: {_error(exc)}")
    code = resp.status_code
    sent_note = f" with {sent}" if sent else ""
    from starlette.routing import Mount

    if code == 404 and isinstance(t.leaf, Mount):
        return Line("ok", shown, "404: no index page here")
    if code >= 500 or code == 405 or (code == 404 and not has_params):
        return Line("FAIL", shown, f"{code}{sent_note}: {_first(resp.text)}")
    errors = _run_errors(resp)
    if errors:
        return Line("FAIL", shown, f"{code}{sent_note}, but the run failed: {errors}")
    return Line("ok", shown, f"{code}{sent_note}")


def _slash_twin(routes: Sequence[Any], t: _Target, path: str) -> str:
    """A route reached only with a trailing slash (``Route("/")`` under
    ``asgi("/tickets")``) while something else answers the path without it:
    the client calling ``/tickets`` never reaches it."""
    if path == "/" or not path.endswith("/"):
        return ""
    bare = path.rstrip("/")
    scope = {"type": "http", "path": bare, "root_path": "", "method": t.method, "headers": []}
    leaf, how = _resolve(routes, scope)
    if leaf is None or leaf is t.leaf:
        return ""
    status = "405" if how == "partial" else "answered"
    return f"only {path} reaches {t.label}; {bare} is {status} by {_label(leaf)}. {PREFIX_HINT}"


def _run_errors(resp: Any) -> str:
    if "json" not in resp.headers.get("content-type", ""):
        return ""
    try:
        data = resp.json()
    except ValueError:
        return ""
    errs = data.get("$errors") if isinstance(data, dict) else None
    if not errs:
        return ""
    first = errs[0] if isinstance(errs, list) else errs
    if isinstance(first, dict):
        first = first.get("error") or first.get("message") or first
    return _first(str(first))


def _routes(root: Path, call: bool) -> List[Line]:
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # starlette's notice about httpx: not the project's
        from starlette.testclient import TestClient

    from operonx.app import Application
    from operonx.lint.build import _env_contract

    lines: List[Line] = []
    with _env_contract(root):
        app = Application.find(root)
        app.bootstrap()
        if call:
            _fake_models()
        served = [s for s in app.services if s.kind != "schedule" and (s.kind != "asgi" or s.app)]
        _SERVICES.clear()
        _SERVICES.update({(s.method or "POST", s.path): s.name for s in served if s.kind != "asgi"})
        for port in sorted({s.port for s in served}, key=str):
            names = [s.name for s in served if s.port == port]
            try:
                asgi = app.asgi(port=port, only=names)
            except Exception as exc:
                lines.append(Line("FAIL", f"listener :{port}", _error(exc)))
                continue
            try:
                with TestClient(asgi, raise_server_exceptions=True) as client:
                    for t in _targets(asgi.routes):
                        lines.append(_probe(client, asgi.routes, t, call))
            except Exception as exc:  # its startup failed
                lines.append(Line("FAIL", f"listener :{port}", f"startup: {_error(exc)}"))
    return lines


# -- the command ------------------------------------------------------------


def run_check(root: Path, *, tests: bool = True, call: bool = False) -> Tuple[List[Line], int]:
    """Every check's line, and the exit code (1 when one failed)."""
    root = Path(root).resolve()
    lines = [_lint(root)]
    lines.append(_tests(root) if tests else Line("--", "tests", "--no-tests"))
    build, loaded = _build(root)
    lines.append(build)
    if loaded:
        cwd = os.getcwd()
        os.chdir(root)  # resources.yaml and .env are read from here
        try:
            lines.extend(_routes(root, call))
        except Exception as exc:
            lines.append(Line("FAIL", "routes", _error(exc)))
        finally:
            os.chdir(cwd)
    failed = sum(1 for x in lines if x.state == "FAIL")
    return lines, 1 if failed else 0


def _print(name: str, lines: List[Line]) -> None:
    print(f"operonx check: {name}")
    routes = [x for x in lines[3:] if x.what.split()[0] in ("GET", "POST", "PUT", "PATCH", "DELETE")]
    hidden = 0
    for x in lines:
        if x in routes and len(routes) > ROUTE_LINES and x.state == "ok":
            hidden += 1
            continue
        print(x)
    if hidden:
        print(f"  ok    {hidden} more route(s)")
    failed = sum(1 for x in lines if x.state == "FAIL")
    checked = sum(1 for x in lines if x.state != "--")
    print(f"FAIL: {failed} of {checked} checks" if failed else f"ok: {checked} checks passed")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="operonx check",
        description="Lint, the tests, every graph built, every route reached: one verdict.",
    )
    parser.add_argument("dir", nargs="?", default=None, help="the project (default: here or above)")
    parser.add_argument(
        "--call",
        action="store_true",
        help="also call each POST/PUT/PATCH route once, with placeholder values and a fake model "
        "(what the route writes is written)",
    )
    parser.add_argument("--no-tests", action="store_true", help="skip pytest")
    args = parser.parse_args(argv)

    start = Path(args.dir) if args.dir else Path.cwd()
    root = next((p for p in (start, *start.resolve().parents) if (p / "operonx.toml").is_file()), None)
    if root is None:
        print(f"operonx check: no operonx.toml at or above {start}", file=sys.stderr)
        return 2
    lines, rc = run_check(root, tests=not args.no_tests, call=args.call)
    _print(root.resolve().name, lines)
    return rc
