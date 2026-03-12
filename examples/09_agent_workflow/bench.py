"""09 Agent Workflow — Test serve modes: Python (FastAPI) and Rust (Axum).

Endpoints tested:
  POST /agent   — {query} → {answer}

Note: LLM agent endpoints tested sequentially (low N) to avoid API rate limits.

Chạy: cd examples && uv run python 09_agent_workflow/bench.py
"""

import asyncio
import os
import socket
import statistics
import subprocess
import sys
import time
import urllib.request

import aiohttp

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

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
    proc = subprocess.Popen(
        [sys.executable, script],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if not wait_ready(port):
        proc.terminate()
        stderr = proc.stderr.read().decode(errors="replace")
        raise RuntimeError(f"Server {script} failed to start:\n{stderr}")
    return proc


def kill_server(proc):
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


async def test_serve(label, script, port):
    try:
        proc = spawn_server(script, port)
    except RuntimeError as e:
        print(f"  SKIP — {e}")
        return

    try:
        async with aiohttp.ClientSession() as session:
            # Warmup
            async with session.post(
                f"http://127.0.0.1:{port}/agent",
                json={"query": "warmup"},
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                await resp.json()

            # Measure N requests sequentially (LLM agent — avoid rate limits)
            times = []
            result = None
            for _ in range(N_REQUESTS):
                start = time.perf_counter()
                async with session.post(
                    f"http://127.0.0.1:{port}/agent",
                    json={"query": "What is 25 * 4 + 100?"},
                    timeout=aiohttp.ClientTimeout(total=60),
                ) as resp:
                    result = await resp.json()
                elapsed = (time.perf_counter() - start) * 1000
                times.append(elapsed)
            median = statistics.median(times)
            answer = result.get("answer", "N/A")
            print(f"  Answer: {str(answer)[:150]}")
            print(f"  Latency: {median:.2f}ms (median of {N_REQUESTS})")
    except Exception as e:
        print(f"  ERROR — {e}")
    finally:
        kill_server(proc)


async def main():
    print("=" * 55)
    print("1. Hush serve (Python backend — FastAPI + uvicorn)")
    print("=" * 55)
    await test_serve("Python", "09_agent_workflow/serve_python.py", PORT_PY)

    print()
    print("=" * 55)
    print("2. Hush serve (Rust backend — Axum)")
    print("=" * 55)
    await test_serve("Rust", "09_agent_workflow/serve_rust.py", PORT_RS)


if __name__ == "__main__":
    asyncio.run(main())
