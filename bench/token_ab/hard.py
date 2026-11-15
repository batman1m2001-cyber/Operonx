"""Hidden robustness probes (COST_PLAN round 3), run on a finished workspace, never shown to an agent.

The task checks in ``accept.py`` ask whether a feature works once. These ask whether the service holds
up: SPEC.md's contract under restart, bad input, load and a model outage, and whether every earlier
task still passes after the later ones. Both arms are held to the same probes; none needs a real model.

    python hard.py WORKSPACE PORT_BASE [--upto T9]   # one JSON line per probe

``PORT_BASE`` is the fake model's port; the service gets ``PORT_BASE + 1``.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import accept  # noqa: E402
from accept import Fail, _mode, _post, service  # noqa: E402


def restart(ws, port, llm):
    """Tickets live in $DATA_DIR: a second ./run.sh on the same folder answers them."""
    data = Path(tempfile.mkdtemp(prefix="ab-hard-"))
    with service(ws, port, llm, data=data) as (c, job, _):
        ids = [_post(c, t)["id"] for t in list(accept.TICKETS)[:2]]
    with service(ws, port, llm, data=data) as (c, job, _):
        got = [t.get("id") for t in c.get("/tickets").json()]
        if sorted(map(str, got)) != sorted(map(str, ids)):
            raise Fail(f"after a restart GET /tickets has ids {got}; want {ids}")


def bad_input(ws, port, llm):
    """A body without text, a text that is not a string, or no JSON: a 4xx, and the service stays up."""
    with service(ws, port, llm) as (c, job, _):
        cases = [("{}", {"json": {}}), ('{"text": 5}', {"json": {"text": 5}}),
                 ("not json", {"content": b"hello", "headers": {"content-type": "application/json"}})]
        for name, kw in cases:
            r = c.post("/tickets", **kw)
            if not 400 <= r.status_code < 500:
                raise Fail(f"POST /tickets {name} answered {r.status_code}; want a 4xx")
        if c.get("/tickets").status_code != 200 or c.get("/tickets").json():
            raise Fail("after bad input GET /tickets is not 200 [] (a bad body was stored?)")


def parallel(ws, port, llm):
    """20 creates at once: 20 tickets, 20 ids, nothing lost."""
    with service(ws, port, llm) as (c, job, _):
        texts = [f"Refund request number {i} for invoice {i}" for i in range(20)]
        with ThreadPoolExecutor(20) as pool:
            rows = list(pool.map(lambda t: httpx.post(f"http://127.0.0.1:{port}/tickets", json={"text": t},
                                                      timeout=60), texts))
        bad = [r.status_code for r in rows if r.status_code not in (200, 201)]
        if bad:
            raise Fail(f"20 parallel POST /tickets: {len(bad)} answered {sorted(set(bad))}")
        ids = {str(r.json().get("id")) for r in rows}
        stored = c.get("/tickets").json()
        if len(ids) != 20 or len(stored) != 20:
            raise Fail(f"20 parallel creates gave {len(ids)} distinct ids and {len(stored)} stored tickets")


def outage(ws, port, llm):
    """The model down for good: POST /tickets answers (an error, not a hang) and nothing half-made is kept;
    once the model is back the service works without a restart."""
    with service(ws, port, llm) as (c, job, _):
        _mode(llm, False, down=True)
        try:
            t0 = time.time()
            try:
                r = c.post("/tickets", json={"text": "I want a refund for invoice 42"}, timeout=60)
                code = r.status_code
            except httpx.TimeoutException:
                raise Fail("POST /tickets with the model down did not answer within 60 s")
            if code in (200, 201) and r.json().get("category") not in accept.TICKETS.values():
                raise Fail(f"POST /tickets with the model down stored a ticket with category "
                           f"{r.json().get('category')!r}")
            if time.time() - t0 > 45:
                raise Fail(f"POST /tickets with the model down took {time.time() - t0:.0f} s")
        finally:
            _mode(llm, False)
        t = _post(c, "The app crashes when I open settings")
        if t.get("category") != "technical":
            raise Fail(f"after the outage POST /tickets → {t}")


def bad_date(ws, port, llm):
    """A date that is not YYYY-MM-DD is the client's mistake: a 4xx, never a 500."""
    with service(ws, port, llm) as (c, job, _):
        for d in ("not-a-date", "2026-13-45"):
            r = c.get(f"/summaries/{d}")
            if not 400 <= r.status_code < 500:
                raise Fail(f"GET /summaries/{d} answered {r.status_code}; want a 4xx")


def unicode(ws, port, llm):
    """Text comes back as it was sent."""
    text = "Tôi muốn hoàn tiền hóa đơn 42 — refund 💸 «now»"
    with service(ws, port, llm) as (c, job, _):
        t = _post(c, text)
        if t.get("text") != text or c.get("/tickets").json()[0].get("text") != text:
            raise Fail(f"the text came back as {t.get('text')!r}")


def empty_jobs(ws, port, llm):
    """Every job runs on an empty store and exits 0; an unknown job exits non-zero."""
    with service(ws, port, llm) as (c, job, _):
        for name in ("daily_summary", "category_report"):
            job(name)
        env = accept._env(ws, port, llm, Path(tempfile.mkdtemp(prefix="ab-hard-")))
        run = subprocess.run(["bash", "./job.sh", "no_such_job"], cwd=ws, env=env, capture_output=True,
                             timeout=180)
        if run.returncode == 0:
            raise Fail("./job.sh no_such_job exited 0")


def long_text(ws, port, llm):
    """A 200 kB ticket: stored, or refused with a 4xx (413); never a 500, and the service stays up."""
    with service(ws, port, llm) as (c, job, _):
        r = c.post("/tickets", json={"text": "invoice refund " * 14000})
        if not (r.status_code in (200, 201) or 400 <= r.status_code < 500):
            raise Fail(f"POST /tickets with 200 kB of text answered {r.status_code}")
        _post(c, "The app crashes when I open settings")


def busy_job(ws, port, llm):
    """A job and 20 creates at the same time: no create fails, none is lost, the job exits 0."""
    with service(ws, port, llm) as (c, job, _):
        for t in list(accept.TICKETS):
            _post(c, t)
        _mode(llm, False, delay_ms=50)
        try:
            with ThreadPoolExecutor(21) as pool:
                jobs = [pool.submit(job, "category_report"), pool.submit(job, "daily_summary")]
                rows = list(pool.map(lambda i: httpx.post(f"http://127.0.0.1:{port}/tickets",
                                                          json={"text": f"Payment {i} failed"}, timeout=60),
                                     range(20)))
                for j in jobs:
                    j.result()
        finally:
            _mode(llm, False)
        bad = [r.status_code for r in rows if r.status_code not in (200, 201)]
        stored = c.get("/tickets").json()
        if bad or len(stored) != 24:
            raise Fail(f"creates during two jobs: {len(bad)} failed {sorted(set(bad))}, {len(stored)} of 24 kept")


PROBES = {"restart": restart, "bad_input": bad_input, "parallel": parallel, "outage": outage,
          "bad_date": bad_date, "unicode": unicode, "empty_jobs": empty_jobs, "long_text": long_text,
          "busy_job": busy_job}


def probe_all(ws: Path, port: int, llm: int, upto: str) -> list:
    """Every probe, then every task check up to *upto*, with the fake model already on *llm*."""
    rows = [_one(name, lambda: probe(ws, port, llm)) for name, probe in PROBES.items()]
    order = list(accept.CHECKS)
    for task in order[: order.index(upto) + 1]:
        ok, why = accept.check(task, ws, port, llm)
        rows.append({"probe": f"regress:{task}", "pass": ok, "why": why[:300]})
    return rows


def run(ws: Path, base: int, upto: str) -> list:
    fake = subprocess.Popen([sys.executable, str(HERE / "fake_llm.py"), str(base)])
    time.sleep(1)
    try:
        return probe_all(ws, base + 1, base, upto)
    finally:
        fake.terminate()


def _one(name, fn) -> dict:
    try:
        fn()
        return {"probe": name, "pass": True, "why": ""}
    except Fail as exc:
        return {"probe": name, "pass": False, "why": str(exc)[:300]}
    except Exception as exc:  # a crash is the product's answer too
        return {"probe": name, "pass": False, "why": f"{type(exc).__name__}: {exc}"[:300]}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("ws")
    ap.add_argument("base", type=int)
    ap.add_argument("--upto", default="T9")
    a = ap.parse_args()
    for row in run(Path(a.ws), a.base, a.upto):
        print(json.dumps(row), flush=True)
