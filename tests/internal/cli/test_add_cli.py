"""`operonx add`: a route, a job, a model call or a page as one command.

In the token A/B (COST_PLAN K2), a new feature cost the agent about eight
turns of reading the template and wiring it by hand. `add` writes the wired
code, prints the lines to fill in, and what it writes passes `operonx check`
before anything is filled in.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from operonx.cli.main import main

pytestmark = pytest.mark.unit


def _project(tmp_path: Path, *parts: str) -> Path:
    root = tmp_path / "p"
    assert main(["init", str(root), *parts]) == 0
    return root


def _add(root: Path, *args: str) -> int:
    return main(["add", "--dir", str(root), *args])


def test_a_route_with_a_model_call_is_wired_ahead_of_the_web_mount_and_checks_clean(
    tmp_path, capsys
):
    root = _project(tmp_path, "--http", "--web")
    capsys.readouterr()
    assert _add(root, "route", "create_ticket", "POST", "/tickets", "--in", "text", "--llm") == 0
    said = capsys.readouterr().out
    assert "src/tickets.py:" in said and "TODO create_ticket" in said
    assert said.rstrip().endswith("operonx check --call")

    main_py = (root / "app" / "main.py").read_text()
    assert "from tickets import SERVICES as TICKETS_SERVICES" in main_py
    assert "services=[*SERVICES, *TICKETS_SERVICES, WEB]" in main_py
    feature = (root / "src" / "tickets.py").read_text()  # one file: op, graph, route
    assert "START >> ask >> step >> END" in feature and 'answer=ask["content"]' in feature
    assert 'http("POST", "/tickets", port=PORT)' in feature
    assert "import create_ticket" not in feature  # the module does not import itself
    # the model is turned on: resource, env names, extra, and a fake one for the tests
    assert "\nllm:assistant:\n  api_type: openai" in (root / "resources.yaml").read_text()
    assert "\nLLM_API_KEY=" in (root / ".env.example").read_text()
    assert '"operonx[openai,serve]' in (root / "pyproject.toml").read_text()
    assert "api_type: fake" in (root / "tests" / "conftest.py").read_text()

    env = {k: v for k, v in os.environ.items() if k not in ("VIRTUAL_ENV", "LLM_API_KEY")}
    env["PYTHONPATH"] = str(root / "src")
    got = subprocess.run(
        [sys.executable, "-m", "operonx.cli.main", "check", "--call", "."],
        cwd=root, env=env, capture_output=True, text=True, timeout=300,
    )  # fmt: skip
    assert got.returncode == 0, got.stdout + got.stderr
    assert "POST /tickets          200 with {'text': 'check'}" in got.stdout
    assert "tests" in got.stdout and "passed" in got.stdout  # the old tests run on the fake model


def test_a_second_route_of_the_feature_joins_its_list_and_the_path_fills_the_op(tmp_path):
    root = _project(tmp_path, "--http")
    assert _add(root, "route", "create_ticket", "POST", "/tickets", "--in", "text") == 0
    assert _add(root, "route", "find_ticket", "GET", "/tickets/{id}") == 0
    main_py = (root / "app" / "main.py").read_text()
    assert main_py.count("from tickets import") == 1
    feature = (root / "src" / "tickets.py").read_text()
    assert feature.count("Service(\n") == 2
    assert "def find_ticket(id: str) -> dict:" in feature and '"$status"' in feature
    # the second op and graph go above SERVICES, which names them
    assert feature.index("def find_ticket_flow") < feature.index("SERVICES = [")


def test_a_route_of_the_template_feature_goes_into_its_file_above_its_lists(tmp_path):
    root = _project(tmp_path, "--http")
    before = (root / "app" / "main.py").read_text()
    assert (
        _add(root, "route", "farewell", "POST", "/bye", "--in", "id", "--feature", "greeting") == 0
    )
    assert (root / "app" / "main.py").read_text() == before  # SERVICES is wired already
    feature = (root / "src" / "greeting.py").read_text()
    assert (
        feature.index("def farewell_flow")
        < feature.index("JOBS = [")
        < feature.index('"farewell",')
    )


def test_a_feature_split_into_a_folder_gets_the_layered_files(tmp_path):
    root = _project(tmp_path, "--http")
    (root / "src" / "billing").mkdir()
    (root / "src" / "billing" / "__init__.py").write_text('"""Billing."""\n')
    assert (
        _add(root, "route", "charge", "POST", "/charge", "--in", "amount", "--feature", "billing")
        == 0
    )
    pkg = root / "src" / "billing"
    assert "def charge(amount: str)" in (pkg / "ops.py").read_text()
    assert "from billing.ops import charge" in (pkg / "graph.py").read_text()
    assert "from billing.graph import charge_flow" in (pkg / "routes.py").read_text()
    assert (
        "from billing.routes import SERVICES as BILLING_SERVICES"
        in (root / "app" / "main.py").read_text()
    )


def test_a_job_with_a_model_call_and_no_inputs_reads_what_an_op_gathers(tmp_path):
    root = _project(tmp_path)  # no --http: APP has jobs=JOBS only
    assert _add(root, "job", "daily_summary", "--llm", "--at", "07:00") == 0
    main_py = (root / "app" / "main.py").read_text()
    assert "jobs=[*JOBS, *DAILY_SUMMARY_JOBS]" in main_py
    feature = (root / "src" / "daily_summary.py").read_text()
    assert "START >> given >> ask >> step >> END" in feature and 'text=given["text"]' in feature
    assert 'schedule=schedule(at="07:00")' in feature and "items=[{}]" in feature


def test_a_clock_in_a_served_project_rides_on_the_routes_port(tmp_path):
    # gate 1: `schedule(at=...)` alone listened on 8000; with $PORT set the app
    # had two listeners and `APP.asgi()` (every route test) refused to build
    root = _project(tmp_path, "--http")
    assert _add(root, "job", "nightly", "--at", "02:00") == 0
    assert 'schedule=schedule(at="02:00", port=PORT)' in (root / "src" / "nightly.py").read_text()
    probe = "from operonx.app import Application; Application.find('.').asgi(); print('one')"
    out = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=root,
        env={**os.environ, "PORT": "8871", "PYTHONPATH": f"{root / 'src'}{os.pathsep}{root}"},
        capture_output=True,
        text=True,
    )
    assert out.stdout.strip() == "one", out.stderr[-800:]


def test_a_model_call_on_its_own_is_a_graph(tmp_path):
    root = _project(tmp_path)
    assert _add(root, "llm", "classify", "--feature", "tickets") == 0
    feature = (root / "src" / "tickets.py").read_text()
    assert "def classify_flow(text):" in feature and "START >> ask >> END" in feature
    assert "@op" not in feature


def test_a_page_reads_a_route_draws_a_table_and_is_linked_from_home(tmp_path):
    root = _project(tmp_path, "--http", "--web")
    assert _add(root, "page", "stats", "--data", "/tickets") == 0
    assert (
        '<script type="module" src="/pages/stats.js">'
        in (root / "web" / "stats" / "index.html").read_text()
    )
    js = (root / "web" / "pages" / "stats.js").read_text()
    assert 'await get("/tickets")' in js and "fetch(" not in js  # lint C17: only web/lib fetches
    assert "export async function get(path)" in (root / "web" / "lib" / "api.js").read_text()
    assert '<a href="/stats/">Stats</a>' in (root / "web" / "index.html").read_text()


def test_a_taken_name_or_a_project_with_no_port_writes_nothing(tmp_path, capsys):
    root = _project(tmp_path, "--http")
    assert _add(root, "route", "greet", "POST", "/hi", "--feature", "greeting") == 2
    assert "greet is already in src/greeting.py" in capsys.readouterr().err
    plain = tmp_path / "q"
    assert main(["init", str(plain)]) == 0
    before = (plain / "app" / "main.py").read_text()
    assert _add(plain, "route", "create", "POST", "/tickets") == 2
    assert "operonx init . --http" in capsys.readouterr().err
    assert (plain / "app" / "main.py").read_text() == before
    assert not (plain / "src" / "tickets.py").exists()
