"""01 Hello World — Run workflow with Hush Python engine.

Chạy: cd examples && uv run python ex01_hello_world/demo.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncio

from hush.core import Hush

from ex01_hello_world.workflow import build_chain, build_hello, build_parallel

EXAMPLES_DIR = Path(__file__).resolve().parents[1]
ENV_FILE = str(EXAMPLES_DIR / ".env")
RESOURCES_FILE = str(EXAMPLES_DIR.parent / "resources.yaml")


async def main():
    print("=" * 50)
    print("1. Hello World")
    print("=" * 50)
    result = await Hush(build_hello(), env=ENV_FILE, resources=RESOURCES_FILE).run(
        inputs={"name": "Hush"}
    )
    print(f"  {result['greeting']}")

    print()
    print("=" * 50)
    print("2. Chain: greet → uppercase")
    print("=" * 50)
    result = await Hush(build_chain(), env=ENV_FILE, resources=RESOURCES_FILE).run(
        inputs={"name": "Hush User"}
    )
    print(f"  Greeting: {result['greeting']}")
    print(f"  Uppercase: {result['result']}")

    print()
    print("=" * 50)
    print("3. Parallel → merge")
    print("=" * 50)
    result = await Hush(build_parallel(), env=ENV_FILE, resources=RESOURCES_FILE).run(inputs={})
    print(f"  Combined: {result['combined']}")


if __name__ == "__main__":
    asyncio.run(main())
