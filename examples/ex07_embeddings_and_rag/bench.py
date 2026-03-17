"""07 Embeddings & RAG — Test serve modes: Python (FastAPI) and Rust (Axum).

Endpoints tested:
  POST /embed   — embed texts → vectors
  POST /rag     — query + documents + doc_vectors → answer + context_docs

Note: Uses external embedding/LLM APIs — tested sequentially to avoid rate limits.

Chạy: cd examples && uv run python ex07_embeddings_and_rag/bench.py
"""

import asyncio
import os
import socket
import statistics
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import aiohttp
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

            await asyncio.gather(
                *[_warmup("/embed", {"texts": ["warmup"]}) for _ in range(N_REQUESTS)]
            )

            # 1. Test /embed endpoint
            print("\n  --- /embed ---")
            start = time.perf_counter()
            async with session.post(
                f"http://127.0.0.1:{port}/embed",
                json={"texts": ["Xin chào!", "Hello"]},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                result = await resp.json()
            elapsed = (time.perf_counter() - start) * 1000
            vectors = result.get("vectors", [])
            print(f"  Vectors: {len(vectors)}, dims: {len(vectors[0]) if vectors else 0}")
            print(f"  Latency: {elapsed:.0f}ms")

            # 2. Pre-compute doc embeddings for RAG
            print("\n  --- /rag (pre-embedding docs) ---")
            async with session.post(
                f"http://127.0.0.1:{port}/embed",
                json={"texts": DOCUMENTS},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                embed_result = await resp.json()
            doc_vectors = embed_result.get("vectors", [])

            # 3. Test /rag endpoint sequentially (LLM API — avoid rate limits)
            query = "Thủ đô Việt Nam là gì?"
            times = []
            result = None
            for _ in range(N_REQUESTS):
                start = time.perf_counter()
                async with session.post(
                    f"http://127.0.0.1:{port}/rag",
                    json={
                        "query": query,
                        "documents": DOCUMENTS,
                        "doc_vectors": doc_vectors,
                    },
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    result = await resp.json()
                elapsed = (time.perf_counter() - start) * 1000
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


async def main():
    print("=" * 55)
    print("1. Hush serve (Python backend — FastAPI + uvicorn)")
    print("=" * 55)
    await test_serve("Python", "ex07_embeddings_and_rag/serve_python.py", PORT_PY)

    print()
    print("=" * 55)
    print("2. Hush serve (Rust backend — Axum)")
    print("=" * 55)
    await test_serve("Rust", "ex07_embeddings_and_rag/serve_rust.py", PORT_RS)


if __name__ == "__main__":
    asyncio.run(main())
