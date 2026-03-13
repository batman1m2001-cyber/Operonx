"""02 Data Pipeline — Run workflows with Hush Python engine.

Chạy: cd examples && uv run python 02_data_pipeline/demo.py
"""

import asyncio
import sys

from hush.core import Hush
from workflow import build_data_pipeline, build_text_pipeline

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


async def main():
    print("=" * 50)
    print("Pipeline 1: Data Transformation")
    print("=" * 50)

    result = await Hush(build_data_pipeline()).run(inputs={})
    print(f"  Tổng:           {result['total']}")
    print(f"  Trung bình:     {result['average']}")
    print(f"  Số phần tử:     {result['count']}")

    print()
    print("=" * 50)
    print("Pipeline 2: Text Processing")
    print("=" * 50)

    result = await Hush(build_text_pipeline()).run(
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
