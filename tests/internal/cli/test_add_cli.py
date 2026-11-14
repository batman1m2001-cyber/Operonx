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
    assert "src/tickets/ops.py:" in said and "TODO create_ticket" in said
    assert said.rstrip().endswith("operonx check --call")

    main_py = (root / "app" / "main.py").read_text()
    assert "from tickets.routes import SERVICES as TICKETS_SERVICES" in main_py
    assert "services=[*SERVICES, *TICKETS_SERVICES, WEB]" in main_py
    graph = (root / "src" / "tickets" / "graph.py").read_text()
    assert "START >> ask >> step >> END" in graph and 'answer=ask["content"]' in graph
    assert (
        'http("POST", "/tickets", port=PORT)'
        in (root / "src" / "tickets" / "routes.py").read_text()
    )
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
    assert main_py.count("tickets.routes") == 1
    routes = (root / "src" / "tickets" / "routes.py").read_text()
    assert routes.count("Service(\n") == 2 and "create_ticket_flow, find_ticket_flow" in routes
    ops = (root / "src" / "tickets" / "ops.py").read_text()
    assert "def find_ticket(id: str) -> dict:" in ops and '"$status"' in ops


def test_a_job_with_a_model_call_and_no_inputs_reads_what_an_op_gathers(tmp_path):
    root = _project(tmp_path)  # no --http: APP has jobs=JOBS only
    assert _add(root, "job", "daily_summary", "--llm", "--at", "07:00") == 0
    main_py = (root / "app" / "main.py").read_text()
    assert "jobs=[*JOBS, *DAILY_SUMMARY_JOBS]" in main_py
    graph = (root / "src" / "daily_summary" / "graph.py").read_text()
    assert "START >> given >> ask >> step >> END" in graph and 'text=given["text"]' in graph
    jobs = (root / "src" / "daily_summary" / "jobs.py").read_text()
    assert 'schedule=schedule(at="07:00")' in jobs and "items=[{}]" in jobs


def test_a_model_call_on_its_own_is_a_graph(tmp_path):
    root = _project(tmp_path)
    assert _add(root, "llm", "classify", "--feature", "tickets") == 0
    graph = (root / "src" / "tickets" / "graph.py").read_text()
    assert "def classify_flow(text):" in graph and "START >> ask >> END" in graph
    assert not (root / "src" / "tickets" / "ops.py").exists()


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
    assert "greet is already in src/greeting/ops.py" in capsys.readouterr().err
    plain = tmp_path / "q"
    assert main(["init", str(plain)]) == 0
    before = (plain / "app" / "main.py").read_text()
    assert _add(plain, "route", "create", "POST", "/tickets") == 2
    assert "operonx init . --http" in capsys.readouterr().err
    assert (plain / "app" / "main.py").read_text() == before and not (
        plain / "src" / "tickets"
    ).exists()
