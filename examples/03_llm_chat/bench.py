"""03 LLM Chat — Test serve modes: Python (FastAPI) and Rust (Axum).

Note: LLM endpoints are tested sequentially (low N) to avoid API rate limits.

Chạy: cd examples && uv run python 03_llm_chat/bench.py
"""

import asyncio
import json
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
N_REQUESTS = 3


def check_port_free(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if s.connect_ex(("127.0.0.1", port)) == 0:
            raise RuntimeError(f"Port {port} is already in use. Kill the stale process and retry.")


def wait_ready(port, timeout=30):
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

            await asyncio.gather(*[_warmup("/", {"question": "warmup"}) for _ in range(N_REQUESTS)])

            # Measure N requests sequentially (LLM API — avoid rate limits)
            times = []
            result = None
            for _ in range(N_REQUESTS):
                start = time.perf_counter()
                async with session.post(
                    f"http://127.0.0.1:{port}/",
                    json={"question": "Python là gì? 1 câu."},
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    result = await resp.json()
                elapsed = (time.perf_counter() - start) * 1000
                times.append(elapsed)
            median = statistics.median(times)
            print(f"  Result:  {json.dumps(result, ensure_ascii=False)}")
            print(f"  Latency: {median:.2f}ms (median of {N_REQUESTS})")
    except Exception as e:
        print(f"  ERROR — {e}")
    finally:
        kill_server(proc)


async def main():
    print("=" * 55)
    print("1. Hush serve (Python backend — FastAPI + uvicorn)")
    print("=" * 55)
    await test_serve("Python", "03_llm_chat/serve_python.py", PORT_PY)

    print()
    print("=" * 55)
    print("2. Hush serve (Rust backend — Axum)")
    print("=" * 55)
    await test_serve("Rust", "03_llm_chat/serve_rust.py", PORT_RS)


if __name__ == "__main__":
    asyncio.run(main())
