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
def service(ws: Path, port: int, llm_port: int, data: Path = None, **extra: str):
    data = data or Path(tempfile.mkdtemp(prefix="ab-data-"))
    env = {**_env(ws, port, llm_port, data), **extra}
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
        yield httpx.Client(base_url=base, timeout=30), (lambda name, check=True: _job(ws, env, name, check)), data
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


def _job(ws: Path, env: Dict[str, str], name: str, check: bool = True) -> None:
    run = subprocess.run(["bash", "./job.sh", name], cwd=ws, env=env, capture_output=True, text=True, timeout=180)
    if check and run.returncode != 0:
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


def _mode(llm_port: int, flaky: bool, reset: bool = False, **more) -> None:
    httpx.post(f"http://127.0.0.1:{llm_port}/_mode", json={"flaky": flaky, "reset": reset, **more})


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


def t7(ws, port, llm):
    with service(ws, port, llm) as (c, job, data):
        _mode(llm, False, delay_ms=400)
        try:
            ids = [_post(c, text).get("id") for text in list(TICKETS)[:2]]
        finally:
            _mode(llm, False)
        r = c.get("/timings")
        rows = r.json() if r.status_code == 200 else None
        if not isinstance(rows, list) or len(rows) < 2:
            raise Fail(f"GET /timings → {r.status_code} {r.text[:300]}; want a list of the 2 requests")
        if [x.get("id") for x in rows[:2]] != ids[::-1]:
            raise Fail(f"GET /timings ids {[x.get('id') for x in rows[:2]]}; want {ids[::-1]} (newest first)")
        for x in rows[:2]:
            model, total = x.get("model_ms"), x.get("total_ms")
            if not isinstance(model, (int, float)) or not isinstance(total, (int, float)):
                raise Fail(f"GET /timings row {x}; want numbers total_ms and model_ms")
            if not 380 <= model <= total or total > model + 1500:
                raise Fail(f"GET /timings row {x}: the model took 400 ms; want model_ms ≈ 400 ≤ total_ms")


def _sse(c: httpx.Client, url: str) -> tuple:
    data, events, ctype = [], [], ""
    with c.stream("GET", url) as r:
        ctype = r.headers.get("content-type", "")
        if r.status_code != 200:
            raise Fail(f"GET {url} → {r.status_code} {r.read()[:300]!r}")
        for line in r.iter_lines():
            if line.startswith("data:"):
                v = line[5:]
                data.append(v[1:] if v.startswith(" ") else v)
            elif line.startswith("event:"):
                events.append(line[6:].strip())
    return ctype, data, events


def t8(ws, port, llm):
    with service(ws, port, llm) as (c, job, data):
        _post(c, "I want a refund for invoice 42")
        _post(c, "The app crashes when I open settings")
        _mode(llm, False, reset=True)
        ctype, chunks, events = _sse(c, f"/summaries/{_today()}/stream")
        if "text/event-stream" not in ctype:
            raise Fail(f"GET /summaries/today/stream content-type {ctype!r}; want text/event-stream")
        if _calls(llm)["streamed"] < 1:
            raise Fail("GET /summaries/today/stream did not ask the model with stream: true")
        text = [d for d in chunks if d.strip() and d.strip() != "[DONE]"]
        if len(text) < 3 or not "".join(text).lstrip().startswith("SUMMARY:"):
            raise Fail(f"the stream's data events {chunks[:8]}; want the model's pieces, starting 'SUMMARY:'")
        if "done" not in events:
            raise Fail(f"the stream's events {events}; want an `event: done` at the end")


def t9(ws, port, llm):
    with service(ws, port, llm, SUMMARY_EVERY="2") as (c, job, data):
        _post(c, "I want a refund for invoice 42")
        _post(c, "The app crashes when I open settings")
        _mode(llm, True)
        try:
            for _ in range(30):
                b = c.get(f"/summaries/{_today()}")
                if b.status_code == 200 and b.json().get("count") == 2 \
                        and str(b.json().get("summary", "")).startswith("SUMMARY:"):
                    break
                time.sleep(0.5)
            else:
                raise Fail(f"with SUMMARY_EVERY=2, no summary of the 2 tickets within 15 s: {b.status_code} "
                           f"{b.text[:200]}")
        finally:
            _mode(llm, False)
    with service(ws, port, llm) as (c, job, data):
        _post(c, "I want a refund for invoice 42")
        _post(c, "I forgot my password and cannot log in")
        _post(c, "The app crashes when I open settings")
        _mode(llm, False, reset=True, fail_summary_after=1)
        try:
            job("category_report", check=False)
        finally:
            _mode(llm, False)
        done_first = [k for k in ("billing", "account", "technical") if c.get(f"/reports/{k}").status_code == 200]
        _mode(llm, False, reset=True)
        job("category_report")
        calls = _calls(llm)["summary"]
        if calls != 3 - len(done_first):
            raise Fail(f"the first run reported {done_first} before the model went down; the second run "
                       f"called the model {calls} time(s), want {3 - len(done_first)}")
        for k in ("billing", "account", "technical"):
            b = c.get(f"/reports/{k}").json()
            if b.get("count") != 1 or not str(b.get("summary", "")).startswith("SUMMARY:"):
                raise Fail(f"after the resumed run, /reports/{k} → {b}")


# --- Round 3 (COST_PLAN): the growth tasks T10–T17 -------------------------------------------------------


def _ok(r: httpx.Response, what: str, codes=(200, 201)):
    if r.status_code not in codes:
        raise Fail(f"{what} → {r.status_code} {r.text[:300]}")
    return r.json()


def _agent(c: httpx.Client, name: str, cats: list) -> str:
    a = _ok(c.post("/agents", json={"name": name, "categories": cats}), f"POST /agents {name}")
    if a.get("name") != name or a.get("categories") != cats or "id" not in a:
        raise Fail(f"POST /agents → {a}; want id, name {name!r}, categories {cats}")
    return a["id"]


def t10(ws, port, llm):
    with service(ws, port, llm) as (c, job, data):
        a1, a2 = _agent(c, "Ann", ["billing"]), _agent(c, "Bob", ["billing", "account"])
        if len(_ok(c.get("/agents"), "GET /agents")) != 2:
            raise Fail("GET /agents does not list the 2 agents")
        b = [_post(c, f"I want a refund for invoice {i}") for i in range(3)]
        if [t.get("agent") for t in b] != [a1, a2, a1] or any(t.get("status") != "open" for t in b):
            raise Fail(f"3 billing tickets got agents {[t.get('agent') for t in b]} status "
                       f"{[t.get('status') for t in b]}; want [{a1}, {a2}, {a1}], open")
        closed = _ok(c.post(f"/tickets/{b[0]['id']}/close"), "POST /tickets/{id}/close")
        if closed.get("status") != "closed" or _ok(c.get(f"/tickets/{b[0]['id']}"), "GET /tickets/{id}").get("status") != "closed":
            raise Fail(f"after close the ticket is {closed}; want status closed")
        nxt = _post(c, "Please refund the double charge")
        if nxt.get("agent") != a1:
            raise Fail(f"after Ann's ticket closed (Ann 1 open, Bob 1 open) a billing ticket went to "
                       f"{nxt.get('agent')}; want Ann {a1} (a tie goes to the agent created first)")
        if _post(c, "I forgot my password and cannot log in").get("agent") != a2:
            raise Fail("an account ticket did not go to Bob, the only agent covering account")
        if _post(c, "The app crashes when I open settings").get("agent") is not None:
            raise Fail("a technical ticket (no agent covers it) has an agent; want null")
        if c.get("/tickets/no-such-id").status_code != 404:
            raise Fail("GET /tickets/no-such-id is not 404")


def t11(ws, port, llm):
    with service(ws, port, llm) as (c, job, data):
        t = _post(c, "I want a refund for invoice 42")
        d = _ok(c.post(f"/tickets/{t['id']}/draft"), "POST /tickets/{id}/draft")
        if str(d.get("ticket")) != str(t["id"]) or not str(d.get("draft", "")).startswith("DRAFT:"):
            raise Fail(f"POST /tickets/{{id}}/draft → {d}; want ticket {t['id']} and the model's draft")
        if _ok(c.get(f"/tickets/{t['id']}"), "GET /tickets/{id}").get("draft") != d["draft"]:
            raise Fail("GET /tickets/{id} does not show the draft")
        _mode(llm, False, reset=True)
        ctype, chunks, events = _sse(c, f"/tickets/{t['id']}/draft/stream")
        text = [x for x in chunks if x.strip() and x.strip() != "[DONE]"]
        if "text/event-stream" not in ctype or _calls(llm)["streamed"] < 1 or len(text) < 3 \
                or not "".join(text).lstrip().startswith("DRAFT:") or "done" not in events:
            raise Fail(f"the draft stream: {ctype!r}, data {chunks[:6]}, events {events}; want SSE pieces of "
                       f"the model's streamed draft, then event: done")
        kept = str(_ok(c.get(f"/tickets/{t['id']}"), "GET /tickets/{id}").get("draft", ""))
        if "".join(kept.split()) != "".join("".join(text).split()):  # the framing may move spaces
            raise Fail(f"after the stream the kept draft is {kept[:80]!r}; want the streamed text")
        if c.post("/tickets/no-such-id/draft").status_code != 404:
            raise Fail("POST /tickets/no-such-id/draft is not 404")


def t12(ws, port, llm):
    with service(ws, port, llm) as (c, job, data):
        a = _post(c, "URGENT: the payment failed twice, refund asap")
        b = _post(c, "A question about my invoice")
        t = _post(c, "The app crashes, this is urgent")
        if [x.get("urgency") for x in (a, b, t)] != ["high", "low", "high"]:
            raise Fail(f"urgency {[x.get('urgency') for x in (a, b, t)]}; want high, low, high")
        q = lambda s: [x.get("id") for x in _ok(c.get(f"/tickets?{s}"), f"GET /tickets?{s}")]
        if q("urgency=high") != [t["id"], a["id"]]:
            raise Fail(f"GET /tickets?urgency=high → {q('urgency=high')}; want [{t['id']}, {a['id']}]")
        if q("category=billing&urgency=low") != [b["id"]] or len(q("status=open")) != 3 or q("category=account"):
            raise Fail("GET /tickets filters (category=billing&urgency=low, status=open, category=account) are wrong")


def t13(ws, port, llm):
    with service(ws, port, llm) as (c, job, data):
        _post(c, "I want a refund for invoice 42")
        _mode(llm, False, reset=True)
        job("escalate")
        if _calls(llm)["escalation"] or _ok(c.get("/escalations"), "GET /escalations"):
            raise Fail("with the default SLA (240 min) a new ticket was escalated")
    with service(ws, port, llm, SLA_MINUTES="0") as (c, job, data):
        ids = [_post(c, x)["id"] for x in ("I want a refund for invoice 42", "The app crashes when I open settings",
                                           "I forgot my password and cannot log in")]
        _ok(c.post(f"/tickets/{ids[2]}/close"), "POST /tickets/{id}/close")
        _mode(llm, False, reset=True, down=True)
        try:
            job("escalate", check=False)
        finally:
            _mode(llm, False)
        job("escalate")
        esc = _ok(c.get("/escalations"), "GET /escalations")
        if sorted(str(e.get("ticket")) for e in esc) != sorted(map(str, ids[:2])) \
                or not all(str(e.get("note", "")).startswith("ESCALATION:") for e in esc):
            raise Fail(f"after a run with the model down and one with it back, /escalations → {esc}; want the 2 "
                       f"open tickets {ids[:2]}, each once, with the model's note")
        _mode(llm, False, reset=True)
        job("escalate")
        if _calls(llm)["escalation"] or not _ok(c.get(f"/tickets/{ids[0]}"), "GET /tickets/{id}").get("escalated"):
            raise Fail("a third run called the model again, or the ticket does not say escalated: true")


class _Hook:
    """A webhook receiver: the first *fail* answers are 500."""

    def __init__(self, port: int):
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        self.hits, self.fail, hook = [], 0, self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                import json as _j
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                try:
                    hook.hits.append(_j.loads(body or b"{}"))
                except ValueError:
                    hook.hits.append({"raw": body[:100].decode(errors="replace")})
                code = 500 if hook.fail > 0 else 200
                hook.fail -= 1
                self.send_response(code)
                self.send_header("Content-Length", "0")
                self.end_headers()

        self.server = ThreadingHTTPServer(("127.0.0.1", port), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def wait(self, pred, secs: float = 20.0) -> list:
        end = time.time() + secs
        while time.time() < end:
            got = [h for h in self.hits if pred(h)]
            if got:
                time.sleep(1.0)  # let any extra tries land
                return [h for h in self.hits if pred(h)]
            time.sleep(0.2)
        return []


def t14(ws, port, llm):
    hook = _Hook(port + 3)
    try:
        with service(ws, port, llm, WEBHOOK_URL=f"http://127.0.0.1:{port + 3}/hook") as (c, job, data):
            hook.fail = 1
            t = _post(c, "I want a refund for invoice 42")
            mine = lambda ev, tid: (lambda h: h.get("event") == ev and str((h.get("ticket") or {}).get("id")) == str(tid))
            got = hook.wait(mine("created", t["id"]))
            if len(got) != 2:
                raise Fail(f"a created ticket whose webhook answered 500 once reached it {len(got)} time(s); want 2 "
                           f"(the 500, then the retry). Hits: {hook.hits[:4]}")
            hook.fail = 99
            t0 = time.time()
            t2 = _post(c, "The app crashes when I open settings")
            if time.time() - t0 > 2.5:
                raise Fail(f"with the webhook failing, POST /tickets took {time.time() - t0:.1f} s")
            time.sleep(12)
            n = len([h for h in hook.hits if mine("created", t2["id"])(h)])
            if n != 3:
                raise Fail(f"a webhook that always answers 500 got {n} tries for one ticket; want 3")
            hook.fail = 0
            _ok(c.post(f"/tickets/{t['id']}/close"), "POST /tickets/{id}/close")
            if not hook.wait(mine("closed", t["id"])):
                raise Fail("closing a ticket did not send a closed event")
    finally:
        hook.server.shutdown()


def t15(ws, port, llm):
    with service(ws, port, llm) as (c, job, data):
        a = _post(c, "I want a REFUND for invoice 42")
        _post(c, "The app crashes when I open settings")
        b = _post(c, "Please refund the double charge")
        got = [x.get("id") for x in _ok(c.get("/tickets/search?q=refund"), "GET /tickets/search?q=refund")]
        if got != [b["id"], a["id"]]:
            raise Fail(f"GET /tickets/search?q=refund → {got}; want [{b['id']}, {a['id']}] (any case, newest first)")
        if _ok(c.get("/tickets/search?q=zebra"), "GET /tickets/search?q=zebra") != []:
            raise Fail("GET /tickets/search?q=zebra is not []")
        import json as _j
        n = _j.loads(_render(f"http://127.0.0.1:{port}/search", "document.querySelectorAll('input').length"))
        if not n:
            raise Fail("GET /search has no search box (no <input>)")
        links = _j.loads(_render(f"http://127.0.0.1:{port}/",
                                 "[...document.querySelectorAll('a')].map(a => a.getAttribute('href') || '')"))
        if not any("search" in h for h in links):
            raise Fail(f"/ has no link to /search: {links}")


def t16(ws, port, llm):
    import csv
    import io
    with service(ws, port, llm) as (c, job, data):
        a1 = _agent(c, "Ann", ["billing"])
        odd = 'Refund, please: "invoice 42"\nthanks'
        x = _post(c, odd)
        _post(c, "Please refund the double charge")
        _post(c, "The app crashes when I open settings")
        r = c.get("/tickets.csv")
        if r.status_code != 200 or "text/csv" not in r.headers.get("content-type", ""):
            raise Fail(f"GET /tickets.csv → {r.status_code} {r.headers.get('content-type')}")
        rows = list(csv.reader(io.StringIO(r.text)))
        if rows[0] != ["id", "created", "category", "urgency", "status", "agent", "text"] or len(rows) != 4:
            raise Fail(f"GET /tickets.csv header {rows[0]} and {len(rows) - 1} rows; want the 7 columns, 3 rows")
        if rows[3][0] != str(x["id"]) or rows[3][6] != odd or rows[3][2] != "billing":
            raise Fail(f"the oldest CSV row is {rows[3]}; want the ticket with commas/quotes/newline intact")
        job("agent_digest")
        d = _ok(c.get(f"/agents/{a1}/digest"), "GET /agents/{id}/digest")
        if str(d.get("agent")) != str(a1) or d.get("count") != 2 or not str(d.get("summary", "")).startswith("SUMMARY:"):
            raise Fail(f"GET /agents/{{id}}/digest → {d}; want agent {a1}, count 2, the model's summary")
        a2 = _agent(c, "Cid", ["other"])
        job("agent_digest")
        if c.get(f"/agents/{a2}/digest").status_code != 404:
            raise Fail("an agent with no open tickets has a digest; want 404")


def t17(ws, port, llm):
    with service(ws, port, llm, SLA_MINUTES="0") as (c, job, data):
        _agent(c, "Ann", ["billing"])
        t = _post(c, "I want a refund for invoice 42")
        _ok(c.post(f"/tickets/{t['id']}/draft"), "POST /tickets/{id}/draft")
        job("escalate")
        _ok(c.post(f"/tickets/{t['id']}/close"), "POST /tickets/{id}/close")
        h = _ok(c.get(f"/tickets/{t['id']}/history"), "GET /tickets/{id}/history")
        ev = [x.get("event") for x in h]
        if ev != ["created", "assigned", "drafted", "escalated", "closed"] or not all(x.get("at") for x in h):
            raise Fail(f"history {ev}; want created, assigned, drafted, escalated, closed, each with `at`")
        u = _post(c, "The app crashes when I open settings")
        if [x.get("event") for x in _ok(c.get(f"/tickets/{u['id']}/history"), "history")] != ["created"]:
            raise Fail("an unassigned ticket's history is not just [created]")
        if c.get("/tickets/no-such-id/history").status_code != 404:
            raise Fail("GET /tickets/no-such-id/history is not 404")


CHECKS = {"T1": t1, "T2": t2, "T3": t3, "T4": t4, "T5a": t5a, "T5b": t5b, "T6": t6, "T7": t7, "T8": t8, "T9": t9,
          "T10": t10, "T11": t11, "T12": t12, "T13": t13, "T14": t14, "T15": t15, "T16": t16, "T17": t17}


def check(task: str, ws: Path, port: int, llm_port: int) -> Tuple[bool, str]:
    try:
        CHECKS[task](Path(ws), port, llm_port)
        return True, ""
    except Fail as exc:
        return False, str(exc)
    except Exception as exc:  # a crash in the check is the product's answer too (bad JSON, …)
        return False, f"{type(exc).__name__}: {exc}"
