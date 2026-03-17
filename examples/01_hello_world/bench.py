"""01 Hello World — Test serve modes: Python (FastAPI) and Rust (Axum).

Chạy: cd examples && uv run python 01_hello_world/bench.py
"""

import asyncio
import json
import math
import os
import socket
import statistics
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import aiohttp

PORT_PY = 9001
PORT_RS = 9002
TOTAL = 1000
CCU = 100
N_WARMUP = 5

ENDPOINTS = [
    ("/", {"name": "Hush"}, "Hello World"),
]


def check_port_free(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if s.connect_ex(("127.0.0.1", port)) == 0:
            raise RuntimeError(f"Port {port} is already in use. Kill the stale process and retry.")


def wait_ready(port, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1)
            return True
        except Exception:
            time.sleep(0.3)
    return False


def spawn_server(script, port):
    check_port_free(port)
    env = {**os.environ, "PORT": str(port)}
    log_dir = Path(script).parent / "logs"
    log_dir.mkdir(exist_ok=True)
    log_out = open(log_dir / f"serve_{port}.log", "w")
    proc = subprocess.Popen(
        [sys.executable, script],
        env=env,
        stdout=log_out,
        stderr=log_out,
    )
    proc._log_file = log_out
    if not wait_ready(port):
        proc.terminate()
        log_out.close()
        log_path = log_dir / f"serve_{port}.log"
        raise RuntimeError(f"Server {script} failed to start. See {log_path}")
    return proc


def kill_server(proc):
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    if hasattr(proc, "_log_file"):
        proc._log_file.close()


def report(times, errors):
    times.sort()
    n = len(times)
    if n == 0:
        print(f"    All {errors} requests failed")
        return
    avg = statistics.mean(times)
    med = statistics.median(times)
    p99_idx = min(math.ceil(n * 0.99) - 1, n - 1)
    p99 = times[p99_idx]
    print(f"    avg={avg:.2f}ms  median={med:.2f}ms  p99={p99:.2f}ms  ({n} ok, {errors} err)")


async def bench_endpoint(session, port, path, payload):
    url = f"http://127.0.0.1:{port}{path}"
    sem = asyncio.Semaphore(CCU)
    times = []
    errors = 0

    async def _do_request():
        nonlocal errors
        async with sem:
            start = time.perf_counter()
            try:
                async with session.post(
                    url, json=payload, timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    await resp.json()
                elapsed = (time.perf_counter() - start) * 1000
                times.append(elapsed)
            except Exception:
                errors += 1

    await asyncio.gather(*[_do_request() for _ in range(TOTAL)])
    return times, errors


async def test_serve(label, script, port):
    try:
        proc = spawn_server(script, port)
    except RuntimeError as e:
        print(f"  SKIP — {e}")
        return

    try:
        async with aiohttp.ClientSession() as session:
            # Warmup — concurrent burst to pre-warm thread pools and caches
            async def _warmup(path, payload):
                try:
                    async with session.post(
                        f"http://127.0.0.1:{port}{path}",
                        json=payload,
                        timeout=aiohttp.ClientTimeout(total=30),
                    ) as resp:
                        await resp.json()
                except Exception:
                    pass

            for path, payload, _ in ENDPOINTS:
                await asyncio.gather(*[_warmup(path, payload) for _ in range(CCU)])

            # Bench each endpoint
            for path, payload, name in ENDPOINTS:
                async with session.post(
                    f"http://127.0.0.1:{port}{path}",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    result = await resp.json()
                print(f"  [{name}] {json.dumps(result, ensure_ascii=False)}")
                times, errors = await bench_endpoint(session, port, path, payload)
                report(times, errors)
    except Exception as e:
        print(f"  ERROR — {e}")
    finally:
        kill_server(proc)


async def main():
    print(f"Benchmark: {TOTAL} requests, {CCU} concurrent (aiohttp)")
    print()
    print("=" * 55)
    print("1. Hush serve (Python backend — FastAPI + uvicorn)")
    print("=" * 55)
    await test_serve("Python", "01_hello_world/serve_python.py", PORT_PY)

    print()
    print("=" * 55)
    print("2. Hush serve (Rust backend — Axum)")
    print("=" * 55)
    await test_serve("Rust", "01_hello_world/serve_rust.py", PORT_RS)


if __name__ == "__main__":
    asyncio.run(main())
