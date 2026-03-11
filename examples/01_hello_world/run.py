"""01 Hello World — Run workflow with Hush Python engine.

Chạy: cd examples && uv run python 01_hello_world/run.py
"""

import asyncio

from hush.core import Hush

from workflow import build_hello, build_chain, build_parallel


async def main():
    print("=" * 50)
    print("1. Hello World")
    print("=" * 50)
    result = await Hush(build_hello()).run(inputs={"name": "Hush"})
    print(f"  {result['greeting']}")

    print()
    print("=" * 50)
    print("2. Chain: greet → uppercase")
    print("=" * 50)
    result = await Hush(build_chain()).run(inputs={"name": "Hush User"})
    print(f"  Greeting: {result['greeting']}")
    print(f"  Uppercase: {result['result']}")

    print()
    print("=" * 50)
    print("3. Parallel → merge")
    print("=" * 50)
    result = await Hush(build_parallel()).run(inputs={})
    print(f"  Combined: {result['combined']}")


if __name__ == "__main__":
    asyncio.run(main())
