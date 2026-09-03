"""14 Streaming & Tracing — generator pipelines exercised via engine.run(...).

Tier 1 — pure compute, no API keys. Streaming-via-`engine.start()` is
covered in the docs; here we exercise the generator-op + scheduler
plumbing through normal `engine.run()`.

Run from this directory:

    uv sync
    uv run python main.py
"""

from __future__ import annotations

import asyncio

from operonx.core import END, START, Operon, graph, op


@op
def chunk_text(text: str, chunk_size: int):
    """Generator op — yields one chunk per yield."""
    words = text.split()
    for i in range(0, len(words), chunk_size):
        chunk = " ".join(words[i : i + chunk_size])
        yield {"chunk": chunk, "index": i // chunk_size}


@op
def analyze_chunk(chunk: str, index: int):
    word_count = len(chunk.split())
    has_long_word = any(len(w) > 6 for w in chunk.split())
    score = word_count * 10 + (15 if has_long_word else 0)
    return {
        "result": f"[{index}] {word_count}w score={score}{'*' if has_long_word else ''}",
    }


@op
async def async_counter(n: int):
    """Async generator — yields numbers 1..n with a simulated delay."""
    for i in range(1, n + 1):
        await asyncio.sleep(0.01)
        yield {"number": i, "squared": i * i}


@op
def format_square(number: int, squared: int):
    return {"label": f"{number}^2 = {squared}"}


@graph
def text_pipeline(text, chunk_size):
    """chunk_text (generator) → analyze_chunk."""
    chunker = chunk_text(text=text, chunk_size=chunk_size)
    analyzer = analyze_chunk(chunk=chunker["chunk"], index=chunker["index"])
    START >> chunker >> analyzer >> END


@graph
def async_pipeline(n):
    """async_counter (async generator) → format_square."""
    counter = async_counter(n=n)
    fmt = format_square(number=counter["number"], squared=counter["squared"])
    START >> counter >> fmt >> END


async def main() -> None:
    sample_text = (
        "The streaming architecture enables real-time token delivery from generator "
        "ops through an event queue scheduler with tuple contexts and proper EOF "
        "propagation"
    )

    runs = [
        ("text", text_pipeline(text=sample_text, chunk_size=3), {}),
        ("async_counter", async_pipeline(n=5), {}),
    ]
    for label, g, inputs in runs:
        result = await Operon(g).run(inputs=inputs)
        content = {k: v for k, v in result.items() if k != "$state"}
        print(f"[{label}] {content}")


if __name__ == "__main__":
    asyncio.run(main())

# ── the served front door ───────────────────────────────────────────────
# Every operonx project serves. The [[serve]] block in operonx.toml names
# this graph, `operonx-serve` boots it, and the studio draws it as the
# entry node feeding the flow — no pipeline begins from nowhere.
#
# `ingress` yields one item per request payload and `egress` writes the
# reply back to the caller. Neither names a resource: the run was minted
# by a transport and already carries its session — and with no session the
# same graph still runs under a plain `engine.start()`, so serving costs
# the example nothing.
from operonx.core.serve import egress, ingress


@op
def answer(item=None) -> dict:
    """One request in, this example's reply out."""
    return {"reply": f"ex14 saw: {item!r}"}


@graph
def served():
    request = ingress()
    a = answer(item=request["item"])
    out = egress(item=a["reply"])
    START >> request >> a >> out >> END

