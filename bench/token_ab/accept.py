"""Hidden acceptance checks, one per task (ENGINEERING_ENV_PLAN E7). Black-box: they start ``./run.sh``
and ``./job.sh`` with a fresh ``$DATA_DIR`` and talk HTTP, so both arms are held to the same contract.

``check(task, workspace, port, llm_port) -> (ok, why)``.
"""

from __future__ import annotations

import datetime as dt
import os
import signal
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Tuple

import httpx

PLAYWRIGHT_PY = "/home/thanglq/mr-finance/.venv/bin/python"
CHROME = ("/root/.cache/ms-playwright/chromium_headless_shell-1243/chrome-headless-shell-linux64/"
          "chrome-headless-shell")
TICKETS = {"I want a refund for invoice 42": "billing", "I forgot my password and cannot log in": "account",
           "The app crashes when I open settings": "technical", "Hello, what are your opening hours?": "other"}


class Fail(Exception):
    pass


def _env(ws: Path, port: int, llm_port: int, data: Path) -> Dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in ("VIRTUAL_ENV", "PYTHONPATH")}
    env.update(PORT=str(port), DATA_DIR=str(data), LLM_BASE_URL=f"http://127.0.0.1:{llm_port}/v1",
               LLM_MODEL="fake-model", LLM_API_KEY="test-key")
    return env


@contextmanager
def service(ws: Path, port: int, llm_port: int):
    data = Path(tempfile.mkdtemp(prefix="ab-data-"))
    env = _env(ws, port, llm_port, data)
    log = open(data.parent / f"{data.name}.server.log", "w")
    proc = subprocess.Popen(["bash", "./run.sh"], cwd=ws, env=env, stdout=log, stderr=subprocess.STDOUT,
                            start_new_session=True)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(240):  # a first run may install its dependencies
            if proc.poll() is not None:
                raise Fail(f"./run.sh exited {proc.returncode}: " + _tail(log.name))
            try:
                httpx.get(base + "/tickets", timeout=2)
                break
            except httpx.HTTPError:
                time.sleep(0.5)
        else:
            raise Fail("./run.sh did not answer on $PORT within 120 s: " + _tail(log.name))
        yield httpx.Client(base_url=base, timeout=30), (lambda name: _job(ws, env, name)), data
    finally:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(10)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        log.close()


def _tail(path: str, n: int = 600) -> str:
    try:
        return Path(path).read_text(errors="replace")[-n:]
    except OSError:
        return ""


def _job(ws: Path, env: Dict[str, str], name: str) -> None:
    run = subprocess.run(["bash", "./job.sh", name], cwd=ws, env=env, capture_output=True, text=True, timeout=180)
    if run.returncode != 0:
        raise Fail(f"./job.sh {name} exited {run.returncode}: {(run.stdout + run.stderr)[-600:]}")


def _post(c: httpx.Client, text: str) -> dict:
    r = c.post("/tickets", json={"text": text})
    if r.status_code not in (200, 201):
        raise Fail(f"POST /tickets {text!r} answered {r.status_code}: {r.text[:300]}")
    return r.json()


def _render(url: str, script: str) -> str:
    """The page's text (or what *script* evaluates to) after its JS ran."""
    code = (
        "import sys, json\nfrom playwright.sync_api import sync_playwright\n"
        "with sync_playwright() as p:\n"
        f"    b = p.chromium.launch(executable_path={CHROME!r})\n"
        "    pg = b.new_page()\n"
        f"    pg.goto({url!r}, wait_until='networkidle')\n"
        "    pg.wait_for_timeout(500)\n"
        f"    print(json.dumps(pg.evaluate({script!r})))\n"
        "    b.close()\n"
    )
    run = subprocess.run([PLAYWRIGHT_PY, "-I", "-c", code], capture_output=True, text=True, timeout=90)
    if run.returncode != 0:
        raise Fail(f"could not render {url}: {run.stderr[-400:]}")
    return run.stdout


def _today() -> str:
    return dt.datetime.now(dt.timezone.utc).date().isoformat()


def _calls(llm_port: int) -> dict:
    return httpx.get(f"http://127.0.0.1:{llm_port}/_calls").json()


def _mode(llm_port: int, flaky: bool, reset: bool = False) -> None:
    httpx.post(f"http://127.0.0.1:{llm_port}/_mode", json={"flaky": flaky, "reset": reset})


def t1(ws, port, llm):
    with service(ws, port, llm) as (c, job, data):
        for text, cat in TICKETS.items():
            t = _post(c, text)
            if t.get("category") != cat or "id" not in t or t.get("text") != text or not t.get("created"):
                raise Fail(f"POST /tickets {text!r} → {t}; want category {cat!r}, id, text, created")
        got = c.get("/tickets").json()
        if [t.get("text") for t in got] != list(TICKETS)[::-1]:
            raise Fail(f"GET /tickets is not the 4 tickets newest first: {[t.get('text') for t in got]}")
        # lower(): a page may upper-case its category chips with CSS, and innerText applies text-transform
        page = _render(f"http://127.0.0.1:{port}/", "document.body.innerText").lower()
        for text, cat in TICKETS.items():
            if text.lower() not in page.replace("\\n", " ") and text.lower().replace("'", "\\u2019") not in page:
                raise Fail(f"GET / does not show the ticket {text!r}")
        if "technical" not in page:
            raise Fail("GET / does not show the categories")


def t2(ws, port, llm):
    with service(ws, port, llm) as (c, job, data):
        _post(c, "I want a refund for invoice 42")
        _post(c, "The app crashes when I open settings")
        job("daily_summary")
        r = c.get(f"/summaries/{_today()}")
        if r.status_code != 200 or not str(r.json().get("summary", "")).startswith("SUMMARY:"):
            raise Fail(f"GET /summaries/today → {r.status_code} {r.text[:300]}; want the model's summary")
        if r.json().get("date") != _today():
            raise Fail(f"GET /summaries/today has date {r.json().get('date')!r}")
        if c.get("/summaries/1999-01-01").status_code != 404:
            raise Fail("GET /summaries/1999-01-01 is not 404")


def t3(ws, port, llm):
    with service(ws, port, llm) as (c, job, data):
        _post(c, "I want a refund for invoice 42")
        _post(c, "Please refund the double charge")
        _post(c, "I forgot my password and cannot log in")
        job("category_report")
        r = c.get("/reports/billing")
        if r.status_code != 200:
            raise Fail(f"GET /reports/billing → {r.status_code} {r.text[:300]}")
        b = r.json()
        if b.get("category") != "billing" or b.get("count") != 2 or not str(b.get("summary", "")).startswith("SUMMARY:"):
            raise Fail(f"GET /reports/billing → {b}; want category billing, count 2, the model's summary")
        if c.get("/reports/account").json().get("count") != 1:
            raise Fail("GET /reports/account count is not 1")
        if c.get("/reports/technical").status_code != 404:
            raise Fail("GET /reports/technical (no tickets) is not 404")


def t4(ws, port, llm):
    with service(ws, port, llm) as (c, job, data):
        _mode(llm, False, reset=True)
        job("daily_summary")
        if _calls(llm)["summary"]:
            raise Fail("daily_summary called the model for a day with no tickets")
        b = c.get(f"/summaries/{_today()}").json()
        if b.get("summary") != "No tickets." or b.get("count") != 0:
            raise Fail(f"GET /summaries/today with no tickets → {b}; want summary 'No tickets.', count 0")
        for text in list(TICKETS)[:3]:
            _post(c, text)
        job("daily_summary")
        b = c.get(f"/summaries/{_today()}").json()
        if b.get("count") != 3 or not str(b.get("summary", "")).startswith("SUMMARY:"):
            raise Fail(f"GET /summaries/today with 3 tickets → {b}; want count 3 and the model's summary")


def t5a(ws, port, llm):
    with service(ws, port, llm) as (c, job, data):
        _mode(llm, True)
        try:
            for text, cat in TICKETS.items():
                t = _post(c, text)
                if t.get("category") != cat:
                    raise Fail(f"with a 503 on the first try, {text!r} got {t.get('category')!r}")
        finally:
            _mode(llm, False)


def t5b(ws, port, llm):
    with service(ws, port, llm) as (c, job, data):
        _post(c, "I want a refund for invoice 42")
        _post(c, "The app crashes when I open settings")
        _mode(llm, True)
        try:
            job("category_report")
        finally:
            _mode(llm, False)
        b = c.get("/reports/technical").json()
        if b.get("count") != 1 or not str(b.get("summary", "")).startswith("SUMMARY:"):
            raise Fail(f"category_report with a 503 on the first try → /reports/technical {b}")


def t6(ws, port, llm):
    with service(ws, port, llm) as (c, job, data):
        for text in ("I want a refund for invoice 42", "Please refund the double charge",
                     "The app crashes when I open settings"):
            _post(c, text)
        rows = _render(f"http://127.0.0.1:{port}/stats",
                       "[...document.querySelectorAll('tr')].map(r => r.innerText.toLowerCase())")
        import json as _j
        rows = _j.loads(rows)
        if not any("billing" in r and "2" in r for r in rows) or not any("technical" in r and "1" in r for r in rows):
            raise Fail(f"/stats has no table row billing 2 and technical 1: {rows}")
        links = _j.loads(_render(f"http://127.0.0.1:{port}/",
                                 "[...document.querySelectorAll('a')].map(a => a.getAttribute('href') || '')"))
        if not any("stats" in h for h in links):
            raise Fail(f"/ has no link to /stats: {links}")


CHECKS = {"T1": t1, "T2": t2, "T3": t3, "T4": t4, "T5a": t5a, "T5b": t5b, "T6": t6}


def check(task: str, ws: Path, port: int, llm_port: int) -> Tuple[bool, str]:
    try:
        CHECKS[task](Path(ws), port, llm_port)
        return True, ""
    except Fail as exc:
        return False, str(exc)
    except Exception as exc:  # a crash in the check is the product's answer too (bad JSON, …)
        return False, f"{type(exc).__name__}: {exc}"
