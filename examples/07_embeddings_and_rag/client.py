"""07 Embeddings & RAG — Test serve modes: Python (FastAPI) and Rust (Axum).

Endpoints tested:
  POST /embed   — embed texts → vectors
  POST /rag     — query + documents + doc_vectors → answer + context_docs

Chạy: cd examples && uv run python 07_embeddings_and_rag/client.py
"""

import json
import os
import socket
import statistics
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PORT_PY = 9001
PORT_RS = 9002
N_REQUESTS = 3

# Sample documents for RAG
DOCUMENTS = [
    "Hà Nội là thủ đô của Việt Nam, nằm ở miền Bắc, có hơn 1000 năm lịch sử.",
    "TP.HCM là thành phố lớn nhất Việt Nam về dân số, trung tâm kinh tế phía Nam.",
    "Đà Nẵng là thành phố lớn nhất miền Trung, nổi tiếng với bãi biển Mỹ Khê.",
    "Huế là cố đô của Việt Nam, nổi tiếng với Đại Nội và ẩm thực đặc sắc.",
    "Phú Quốc là đảo lớn nhất Việt Nam, thuộc tỉnh Kiên Giang, nổi tiếng du lịch biển.",
]


def check_port_free(port):
    """Raise if port is already in use."""
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


def wait_ready(port, timeout=15):
    """Poll until server accepts connections."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1)
            return True
        except Exception:
            time.sleep(0.3)
    return False


def spawn_server(script, port):
    """Start a serve script as a background process."""
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
        print(f"  SKIP — {e}")
        return

    try:
        # 1. Test /embed endpoint
        print(f"\n  --- /embed ---")
        result, elapsed = post(port, "/embed", {"texts": ["Xin chào!", "Hello"]})
        vectors = result.get("vectors", [])
        print(f"  Vectors: {len(vectors)}, dims: {len(vectors[0]) if vectors else 0}")
        print(f"  Latency: {elapsed:.0f}ms")

        # 2. Pre-compute doc embeddings for RAG
        print(f"\n  --- /rag (pre-embedding docs) ---")
        embed_result, _ = post(port, "/embed", {"texts": DOCUMENTS})
        doc_vectors = embed_result.get("vectors", [])

        # 3. Test /rag endpoint
        query = "Thủ đô Việt Nam là gì?"
        times = []
        for _ in range(N_REQUESTS):
            result, elapsed = post(port, "/rag", {
                "query": query,
                "documents": DOCUMENTS,
                "doc_vectors": doc_vectors,
            })
            times.append(elapsed)
        median = statistics.median(times)
        print(f"  Q: {query}")
        print(f"  A: {result.get('answer', 'N/A')}")
        print(f"  Sources: {result.get('context_docs', [])[:2]}")
        print(f"  Latency: {median:.0f}ms (median of {N_REQUESTS})")

    except Exception as e:
        print(f"  ERROR — {e}")
    finally:
        kill_server(proc)


def main():
    print("=" * 55)
    print("1. Hush serve (Python backend — FastAPI + uvicorn)")
    print("=" * 55)
    test_serve("Python", "07_embeddings_and_rag/serve_python.py", PORT_PY)

    print()
    print("=" * 55)
    print("2. Hush serve (Rust backend — Axum)")
    print("=" * 55)
    test_serve("Rust", "07_embeddings_and_rag/serve_rust.py", PORT_RS)


if __name__ == "__main__":
    main()
