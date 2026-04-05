"""02 Data Pipeline — Run workflows with Hush Python engine.

Chạy: cd examples && uv run python ex02_data_pipeline/demo.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncio

from hush.core import Hush

from ex02_data_pipeline.workflow import build_data_pipeline, build_text_pipeline

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

EXAMPLES_DIR = Path(__file__).resolve().parents[1]
ENV_FILE = str(EXAMPLES_DIR / ".env")
RESOURCES_FILE = str(EXAMPLES_DIR.parent / "resources.yaml")


async def main():
    print("=" * 50)
    print("Pipeline 1: Data Transformation")
    print("=" * 50)

    result = await Hush(build_data_pipeline(), env=ENV_FILE, resources=RESOURCES_FILE).run(inputs={})
    print(f"  Tổng:           {result['total']}")
    print(f"  Trung bình:     {result['average']}")
    print(f"  Số phần tử:     {result['count']}")

    print()
    print("=" * 50)
    print("Pipeline 2: Text Processing")
    print("=" * 50)

    result = await Hush(build_text_pipeline(), env=ENV_FILE, resources=RESOURCES_FILE).run(
        inputs={
            "text": """
        Trí tuệ nhân tạo   đang thay đổi   cách chúng ta sống
        và   làm việc. Trí tuệ nhân tạo   đã trở thành
        một phần không thể thiếu trong cuộc sống hàng ngày.
        """
        }
    )
    print(f"  Report: {result['report']}")


if __name__ == "__main__":
    asyncio.run(main())
