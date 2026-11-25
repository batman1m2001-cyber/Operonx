"""`operonx check`: lint, tests, graphs and routes in one verdict.

The route checks are the reason it exists: in the token A/B (COST_PLAN), the
agents' costliest loop was a route another mount answered (`asgi("/tickets")`
answers `/tickets/`, not `/tickets`; a mount at `/` listed first answers
every path) found by serving and curling. `check` resolves every route
through the listener's own router and names what answers it instead.

Each check runs in a subprocess: it imports the project's modules, and two
generated projects in one process would share `greeting` and `_shared`.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import operonx
from operonx.cli.main import main

pytestmark = pytest.mark.unit


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "p"
    assert main(["init", str(root), "--http", "--web"]) == 0
    return root


def _check(root: Path, *args: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in ("VIRTUAL_ENV", "LLM_API_KEY")}
    env["PYTHONPATH"] = str(root / "src")
    return subprocess.run(
        [sys.executable, "-m", "operonx.cli.main", "check", *args, "."],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )


def _main_py(root: Path, services: str, extra: str = "") -> None:
    (root / "app" / "main.py").write_text(
        '"""The application, rewired for a test."""\n\n'
        "from operonx.app import Application, Service, asgi\n"
        "from starlette.staticfiles import StaticFiles\n\n"
        "from _shared.config import PORT, ROOT\n"
        "from app.greeting import JOBS, SERVICES\n"
        f"{extra}\n"
        'WEB = Service("web", asgi("/", port=PORT), '
        'app=StaticFiles(directory=ROOT / "web", html=True))\n\n'
        f'APP = Application("p", jobs=JOBS, services={services})\n'
    )


def test_a_new_project_checks_clean_and_its_tests_run(tmp_path):
    root = _project(tmp_path)
    got = _check(root)
    assert got.returncode == 0, got.stdout + got.stderr
    out = got.stdout
    assert "ok    lint" in out and "ok    build" in out
    assert "passed" in out.split("tests", 1)[1].splitlines()[0]  # pytest ran
    assert "POST /api/greet        reaches service greet" in out
    assert "GET  /                 200" in out
    assert out.rstrip().endswith("checks passed")


def test_call_calls_a_post_route_filling_what_the_door_says_is_missing(tmp_path):
    root = _project(tmp_path)
    got = _check(root, "--call", "--no-tests")
    assert got.returncode == 0, got.stdout + got.stderr
    assert "POST /api/greet        200 with {'id': 'check'}" in got.stdout


def test_a_mount_at_root_listed_first_hides_every_route_after_it(tmp_path):
    root = _project(tmp_path)
    _main_py(root, "[WEB, *SERVICES]")
    got = _check(root, "--no-tests")
    assert got.returncode == 1
    line = next(x for x in got.stdout.splitlines() if "POST /api/greet" in x)
    assert line.split()[0] == "FAIL"
    assert "by StaticFiles at /, not service greet" in line
    assert "list it last in APP" in line


def test_a_prefix_mount_is_reached_only_with_a_trailing_slash(tmp_path):
    """The T1 trap: `asgi("/tickets")` around `Route("/")`; `POST /tickets`
    goes to the static files (which answer 405)."""
    root = _project(tmp_path)
    extra = (
        "from starlette.applications import Starlette\n"
        "from starlette.responses import JSONResponse\n"
        "from starlette.routing import Route\n\n\n"
        "async def create(request):\n"
        "    return JSONResponse({}, status_code=201)\n\n\n"
        'TICKETS = Service("tickets", asgi("/tickets", port=PORT), '
        'app=Starlette(routes=[Route("/", create, methods=["POST"])]))\n'
    )
    _main_py(root, "[*SERVICES, TICKETS, WEB]", extra)
    got = _check(root, "--no-tests")
    assert got.returncode == 1
    line = next(x for x in got.stdout.splitlines() if "POST /tickets/" in x)
    assert "only /tickets/ reaches create; /tickets is answered by StaticFiles at /" in line
    assert "Make the route a Service with http(...)" in line
    # and lint names the hand-written route (C14) on its own line
    assert "[C14]" in got.stdout


def test_a_raw_asgi_app_under_a_prefix_is_reached_where_it_is_mounted(tmp_path):
    """COST_PLAN C41: a raw ASGI callable mounted at `/stream` answers its
    own paths; check probed it as if `/stream` needed a trailing slash."""
    root = _project(tmp_path)
    extra = (
        "async def raw(scope, receive, send):\n"
        "    await send({'type': 'http.response.start', 'status': 200, 'headers': []})\n"
        "    await send({'type': 'http.response.body', 'body': b'ok'})\n\n\n"
        'RAW = Service("raw", asgi("/stream", port=PORT), app=raw)\n'
    )
    _main_py(root, "[*SERVICES, RAW, WEB]", extra)
    got = _check(root, "--no-tests")
    assert got.returncode == 0, got.stdout + got.stderr
    assert "/stream" in got.stdout and "FAIL" not in got.stdout


def test_a_get_route_reaches_a_fake_model_not_the_real_one(tmp_path):
    """COST_PLAN C41: check calls every GET; one that reaches a model must
    not call the real one (no key here, an address nothing listens on)."""
    root = tmp_path / "q"
    assert main(["init", str(root), "-t", "chat", "--http"]) == 0
    exposed = root / "app" / "chat.py"
    exposed.write_text(
        exposed.read_text().replace('http("POST", "/api/chat"', 'http("GET", "/api/chat"')
    )
    src = root / "src" / "chat.py"
    text = src.read_text()
    text = text.replace("def chat_flow(question):", 'def chat_flow(question="hi"):')
    text = text.replace(
        'return {"answer": None, "error": error or "the model gave no answer"}',
        'raise RuntimeError(error or "no answer")',  # a real call would be a 500
    )
    src.write_text(text)
    got = subprocess.run(
        [sys.executable, "-m", "operonx.cli.main", "check", "--no-tests", "."],
        cwd=root,
        env={
            **{k: v for k, v in os.environ.items() if k not in ("VIRTUAL_ENV", "LLM_API_KEY")},
            "PYTHONPATH": str(root / "src"),
            "LLM_BASE_URL": "http://127.0.0.1:9/v1",
        },
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert got.returncode == 0, got.stdout + got.stderr
    assert "GET  /api/chat         200" in got.stdout


def test_an_application_that_does_not_load_fails_the_build_and_skips_routes(tmp_path):
    root = _project(tmp_path)
    (root / "app" / "main.py").write_text('"""Broken."""\n\nimport no_such_module  # noqa: F401\n')
    got = _check(root, "--no-tests")
    assert got.returncode == 1
    assert "FAIL  build                  the application:" in got.stdout
    assert "no_such_module" in got.stdout
    assert "POST /api/greet" not in got.stdout


def test_no_project_is_exit_2(tmp_path):
    got = subprocess.run(
        [sys.executable, "-m", "operonx.cli.main", "check", str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert got.returncode == 2 and "no operonx.toml" in got.stderr


def test_a_stale_guide_is_one_line_and_check_writes_nothing(tmp_path):
    root = tmp_path / "p"
    assert main(["init", str(root)]) == 0
    assert "stale" not in _check(root, "--no-tests").stdout
    page = root / ".operonx" / "guide" / "core" / "01-ops.md"
    page.write_text("stale")
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    out = _check(root, "--no-tests").stdout
    notes = [line for line in out.splitlines() if "stale" in line]
    assert len(notes) == 1 and "pages changed" in notes[0] and "`operonx guide`" in notes[0]
    assert operonx.__version__ in notes[0]
    after = {
        p: p.read_bytes() for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts
    }
    assert after == {p: b for p, b in before.items() if "__pycache__" not in p.parts}
