"""04 LLM Advanced — Test serve modes: Python (FastAPI) and Rust (Axum).

Chạy: cd examples && uv run python 04_llm_advanced/client.py
"""

import json
import os
import socket
import statistics
import subprocess
import sys
import time
import urllib.request

PORT_PY = 9001
PORT_RS = 9002
N_REQUESTS = 3


def check_port_free(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if s.connect_ex(("127.0.0.1", port)) == 0:
            raise RuntimeError(
                f"Port {port} is already in use. Kill the stale process and retry."
            )


def post(port, path, data):
    body = json.dumps(data).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    start = time.perf_counter()
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read())
    elapsed = (time.perf_counter() - start) * 1000
    return result, elapsed


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


def test_serve(label, script, port):
    try:
        proc = spawn_server(script, port)
    except RuntimeError as e:
        print(f"  SKIP - {e}")
        return

    try:
        # Warmup
        post(port, "/", {"text": "warmup"})
        # Measure
        times = []
        for _ in range(N_REQUESTS):
            result, elapsed = post(port, "/", {"text": "Sản phẩm tuyệt vời!"})
            times.append(elapsed)
        median = statistics.median(times)
        analysis = json.loads(result["analysis"])
        print(f"  Sentiment: {analysis['sentiment']} ({analysis['confidence']})")
        print(f"  Latency:   {median:.2f}ms (median of {N_REQUESTS})")
    except Exception as e:
        print(f"  ERROR - {e}")
    finally:
        kill_server(proc)


def main():
    print("=" * 55)
    print("1. Hush serve (Python backend - FastAPI + uvicorn)")
    print("=" * 55)
    test_serve("Python", "04_llm_advanced/serve_python.py", PORT_PY)

    print()
    print("=" * 55)
    print("2. Hush serve (Rust backend - Axum)")
    print("=" * 55)
    test_serve("Rust", "04_llm_advanced/serve_rust.py", PORT_RS)


if __name__ == "__main__":
    main()
