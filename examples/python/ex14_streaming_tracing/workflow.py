"""Shared workflow definitions for ex14_streaming_tracing.

Generator pipelines for streaming demos.
"""

import asyncio

from operon.core import END, PARENT, START, GraphOp, op

# =============================================================================
# Generator ops
# =============================================================================


@op
def chunk_text(text: str, chunk_size: int):
    """Generator: splits text into chunks, yielding each one."""
    words = text.split()
    for i in range(0, len(words), chunk_size):
        chunk = " ".join(words[i : i + chunk_size])
        yield {"chunk": chunk, "index": i // chunk_size}


@op
def analyze_chunk(chunk: str, index: int):
    """Analyze a single chunk — runs once per yield from chunk_text."""
    word_count = len(chunk.split())
    has_long_word = any(len(w) > 6 for w in chunk.split())
    score = word_count * 10 + (15 if has_long_word else 0)
    return {
        "result": f"[{index}] {word_count}w score={score}{'*' if has_long_word else ''}",
    }


@op
async def async_counter(n: int):
    """Async generator: yields numbers 1..n with a simulated delay."""
    for i in range(1, n + 1):
        await asyncio.sleep(0.01)
        yield {"number": i, "squared": i * i}


@op
def format_square(number: int, squared: int):
    """Format a single squared value."""
    return {"label": f"{number}^2 = {squared}"}


# =============================================================================
# Graph builders
# =============================================================================


def build_text_pipeline() -> GraphOp:
    """Pipeline: chunk_text (generator) >> analyze >> END."""
    with GraphOp(name="text-chunker") as graph:
        chunker = chunk_text(text=PARENT["text"], chunk_size=PARENT["chunk_size"])
        analyzer = analyze_chunk(chunk=chunker["chunk"], index=chunker["index"])
        START >> chunker >> analyzer >> END
    return graph


def build_async_pipeline() -> GraphOp:
    """Pipeline: async_counter (async generator) >> format_square."""
    with GraphOp(name="async-counter") as graph:
        counter = async_counter(n=PARENT["n"])
        fmt = format_square(number=counter["number"], squared=counter["squared"])
        START >> counter >> fmt >> END
    return graph
