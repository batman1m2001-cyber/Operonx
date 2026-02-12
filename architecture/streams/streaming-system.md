# Streaming System

## Overview

Streaming system cung cấp interface thống nhất để stream data real-time giữa các ops trong workflow. Được thiết kế để hỗ trợ LLM token streaming, event streaming, và bất kỳ loại data nào cần truyền real-time.

Location: `hush-core/hush/core/streams/`

## Tại sao cần Streaming System?

Hush có 2 hệ thống truyền dữ liệu:

| | State System | Streaming System |
|---|---|---|
| **Mục đích** | Lưu trữ kết quả cuối cùng của op | Truyền data real-time giữa ops |
| **Kiểu truy cập** | O(1) key-based lookup | AsyncGenerator (ordered queue) |
| **Thời điểm** | Sau khi op hoàn thành | Trong khi op đang chạy |
| **Use case** | Kết quả workflow | LLM token streaming, events |

State system lưu trữ snapshot cuối cùng. Streaming system truyền data theo thứ tự, real-time, trong khi op còn đang xử lý.

## Kiến trúc

```
┌─────────────────────────────────────────┐
│          BaseStreamingService           │
│  (Abstract Interface)                   │
│  push() | end() | get() | close()      │
└──────────────────┬──────────────────────┘
                   │
        ┌──────────┴──────────┐
        │                     │
┌───────▼──────┐    ┌────────▼────────┐
│ InMemory     │    │ Redis/Kafka/... │
│ StreamService│    │ (future)        │
└──────────────┘    └─────────────────┘
```

### Phân cấp Queue

InMemoryStreamService tổ chức queues theo 3 cấp:

```
session_id
  └── request_id
        └── channel_name → asyncio.Queue
```

- **session_id**: Phân tách theo session (multi-tenant). Mặc định: `"default"`
- **request_id**: Mỗi lần chạy `engine.run()` có một request_id duy nhất
- **channel_name**: Tên channel (thường là `op.identity(context_id)`)

## BaseStreamingService Interface

```python
class BaseStreamingService(ABC):

    @abstractmethod
    async def push(self, request_id, channel_name, data, session_id=None):
        """Push data vào channel."""

    @abstractmethod
    async def end(self, request_id, channel_name, session_id=None):
        """Gửi END signal để kết thúc stream."""

    @abstractmethod
    async def get(self, request_id, channel_name, session_id=None,
                  timeout=0.01, max_idle_time=None) -> AsyncGenerator:
        """Consume data từ channel. Dừng khi gặp END signal hoặc max_idle_time."""

    @abstractmethod
    def close(self):
        """Dọn dẹp resources."""
```

### Tham số quan trọng của `get()`

| Tham số | Mục đích |
|---------|---------|
| `timeout` | Timeout cho mỗi `queue.get()` (mặc định 0.01s). Giúp vòng lặp không bị block vĩnh viễn |
| `max_idle_time` | Thời gian tối đa chờ data mới trước khi dừng. `None` = chờ vô thời hạn đến END signal |

## InMemoryStreamService

Implementation hiệu năng cao cho single-process, sử dụng `asyncio.Queue`.

### Sentinel Pattern

Stream kết thúc bằng sentinel value `"__END__"`:

```python
# Producer (LLMOp)
await STREAM_SERVICE.push(request_id, channel, chunk)  # Gửi từng chunk
await STREAM_SERVICE.end(request_id, channel)           # Gửi __END__

# Consumer
async for data in STREAM_SERVICE.get(request_id, channel):
    process(data)  # Vòng lặp tự dừng khi gặp __END__
```

### Thread Safety

- Sử dụng `asyncio.Lock()` để đảm bảo an toàn khi tạo queue mới
- Queue tạo lazy: chỉ tạo khi có push hoặc get đầu tiên
- Queue tự động dọn dẹp sau khi consumer đọc xong (trong `finally` block của `get()`)

### end_request()

Method đặc biệt để kết thúc **tất cả channels** của một request:

```python
await STREAM_SERVICE.end_request(request_id, session_id)
# Gửi __END__ đến mọi channel đang active cho request này
```

Engine gọi method này sau khi graph execution hoàn thành (`engine.py`).

## Tích hợp với Engine

```python
# Trong engine.py, sau khi chạy xong graph:
await STREAM_SERVICE.end_request(request_id, session_id)
```

Đảm bảo mọi consumer đều nhận được END signal, kể cả khi op không tự gọi `end()`.

## Tích hợp với LLMOp

LLMOp sử dụng STREAM_SERVICE trong streaming mode:

```python
# Trong LLMOp._handle_streaming():
async for chunk in llm.stream(**params):
    # Xử lý chunk (accumulate content)
    response += chunk.choices[0].delta.content or ""

    # Push chunk đến STREAM_SERVICE
    asyncio.create_task(
        STREAM_SERVICE.push(request_id, channel_name, chunk)
    )

# Kết thúc stream
asyncio.create_task(STREAM_SERVICE.end(request_id, channel_name))
```

Consumer (API endpoint, WebSocket, ...) đọc stream:

```python
async for chunk in STREAM_SERVICE.get(request_id, channel_name):
    await websocket.send(chunk)
```

## Global Singleton

```python
# hush/core/streams/__init__.py
STREAM_SERVICE = InMemoryStreamService()
```

Import và sử dụng:

```python
from hush.core.streams import STREAM_SERVICE
```

## Tạo Custom Backend

Để hỗ trợ hệ thống phân tán, tạo backend mới kế thừa `BaseStreamingService`:

```python
from hush.core.streams.base import BaseStreamingService

class RedisStreamService(BaseStreamingService):
    def __init__(self, redis_url: str):
        self._redis = aioredis.from_url(redis_url)

    async def push(self, request_id, channel_name, data, session_id=None):
        key = f"{session_id or 'default'}:{request_id}:{channel_name}"
        await self._redis.rpush(key, serialize(data))

    async def end(self, request_id, channel_name, session_id=None):
        key = f"{session_id or 'default'}:{request_id}:{channel_name}"
        await self._redis.rpush(key, "__END__")

    async def get(self, request_id, channel_name, session_id=None,
                  timeout=0.01, max_idle_time=None):
        key = f"{session_id or 'default'}:{request_id}:{channel_name}"
        while True:
            data = await self._redis.blpop(key, timeout=timeout)
            if data and data[1] == b"__END__":
                break
            if data:
                yield deserialize(data[1])

    def close(self):
        self._redis.close()
```

Thay thế global singleton:

```python
from hush.core import streams
streams.STREAM_SERVICE = RedisStreamService("redis://localhost:6379")
```

## Xem thêm

- [Execution Flow](../engine/execution-flow.md) - Engine gọi `end_request()` như thế nào
- [LLM Abstraction](../providers/llm-abstraction.md) - BaseLLM.stream() interface
- [Workflow Ops](../providers/workflow-ops.md) - LLMOp streaming mode
