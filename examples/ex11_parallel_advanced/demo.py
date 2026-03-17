"""11 Parallel Advanced — Run locally.

Fan-out/fan-in, generator iteration, partial failure handling.

Examples 1-3: no API keys required.

Chạy: cd examples && uv run python ex11_parallel_advanced/demo.py
"""

import asyncio
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")

from hush.core import Hush
from workflow import build_fan_out, build_iteration, build_partial_failure


async def main():
    # 1. Fan-out / Fan-in
    print("=" * 55)
    print("1. Fan-out / Fan-in (3 parallel branches)")
    print("=" * 55)
    engine = Hush(build_fan_out())
    result = await engine.run(
        inputs={"text": "This is a great excellent product with good quality and love it"}
    )
    analysis = result["analysis"]
    print(f"  Sentiment:    {analysis['sentiment']}")
    print(f"  Keywords:     {analysis['keywords']}")
    print(f"  Word count:   {analysis['word_count']}")
    print(f"  Avg word len: {analysis['avg_word_len']}")

    # 2. Generator iteration
    print()
    print("=" * 55)
    print("2. Generator Iteration (replaces MapOp)")
    print("=" * 55)
    engine = Hush(build_iteration())
    items = list(range(1, 10))
    result = await engine.run(inputs={"items": items})
    print(f"  Items:   {items}")
    print(f"  Results: {result['results']}")

    # 3. Partial failure
    print()
    print("=" * 55)
    print("3. Partial Failure Handling")
    print("=" * 55)
    engine = Hush(build_partial_failure())
    result = await engine.run(inputs={"items": [1, 2, 3, 4, 5, 6, 7]})
    results = result["results"]
    errors = result["errors"]
    successful = [r for r, e in zip(results, errors) if e is None]
    failed = [e for e in errors if e is not None]
    print("  Input:      [1, 2, 3, 4, 5, 6, 7]")
    print(f"  Successful: {successful}")
    print(f"  Failed:     {failed}")


if __name__ == "__main__":
    asyncio.run(main())
