"""Package streaming cho workflow.

Cung cấp interface thống nhất để stream data giữa các node trong workflow.
Hỗ trợ nhiều backend khác nhau:
- In-memory: Hiệu năng cao cho single-process
- Redis, Kafka, RabbitMQ: Cho hệ thống phân tán

Cấu trúc phân cấp:
    session_id -> request_id -> channel_name -> Queue

Sử dụng:
    from hush.core.streams import STREAM_SERVICE

    # Push data
    await STREAM_SERVICE.push(request_id, "output", {"key": "value"})

    # Consume data
    async for data in STREAM_SERVICE.get(request_id, "output"):
        process(data)

    # Kết thúc stream
    await STREAM_SERVICE.end(request_id, "output")
"""

from .base import BaseStreamingService
from .memory import InMemoryStreamService

# Instance mặc định
STREAM_SERVICE = InMemoryStreamService()

__all__ = ["BaseStreamingService", "InMemoryStreamService", "STREAM_SERVICE"]
