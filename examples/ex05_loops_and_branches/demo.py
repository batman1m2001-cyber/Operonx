"""05 Loops & Branches — Run workflows with Hush Python engine.

Chạy: cd examples && uv run python ex05_loops_and_branches/demo.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncio

from hush.core import Hush

EXAMPLES_DIR = Path(__file__).resolve().parents[1]
ENV_FILE = str(EXAMPLES_DIR / ".env")
RESOURCES_FILE = str(EXAMPLES_DIR.parent / "resources.yaml")

from ex05_loops_and_branches.workflow import (
    build_branch,
    build_for_loop,
    build_map_op,
    build_while_loop,
)


async def main():
    print("=" * 50)
    print("1. For Loop (generator yield, sequential)")
    print("=" * 50)
    result = await Hush(build_for_loop(), env=ENV_FILE, resources=RESOURCES_FILE).run(
        inputs={"items": ["apple", "banana", "cherry"], "prefix": "Fruit"}
    )
    print(f"  Result: {result['result']}")

    print()
    print("=" * 50)
    print("2. Map Op (generator yield, parallel)")
    print("=" * 50)
    result = await Hush(build_map_op(), env=ENV_FILE, resources=RESOURCES_FILE).run(inputs={"numbers": [1, 2, 3, 4, 5]})
    print("  Input:   [1, 2, 3, 4, 5]")
    print(f"  Squared: {result['squared']}")

    print()
    print("=" * 50)
    print("3. While Loop (generator while, conditional)")
    print("=" * 50)
    result = await Hush(build_while_loop(), env=ENV_FILE, resources=RESOURCES_FILE).run(inputs={"start_value": 256})
    print("  Start: 256")
    print(f"  Values: {result['value']}")

    print()
    print("=" * 50)
    print("4. Branch (if_ conditional routing)")
    print("=" * 50)
    for score in [95, 75, 55, 30]:
        result = await Hush(build_branch(), env=ENV_FILE, resources=RESOURCES_FILE).run(inputs={"score": score})
        print(f"  Score {score}: {result['grade']} — {result['message']}")


if __name__ == "__main__":
    asyncio.run(main())
