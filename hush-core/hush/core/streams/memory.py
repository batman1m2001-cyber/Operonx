import asyncio
import time
from typing import AsyncGenerator, Dict, Set, Optional, Any
from collections import defaultdict
from hush.core.loggings import LOGGER
from .base import BaseStreamingService


class InMemoryStreamService(BaseStreamingService):
    """Streaming service in-memory sử dụng asyncio.Queue.

    Triển khai in-memory hiệu năng cao cho single-process workflow.
    Tổ chức stream theo phân cấp: session_id -> request_id -> channel_name.

    Với hệ thống phân tán, sử dụng Redis, Kafka, hoặc các backend phân tán khác.
    """

    DEFAULT_SESSION = "default"

    def __init__(self):
        # Cấu trúc: {session_id: {request_id: {channel_name: asyncio.Queue}}}
        self._queues: Dict[str, Dict[str, Dict[str, asyncio.Queue]]] = defaultdict(lambda: defaultdict(dict))
        # Lock để đảm bảo thread-safe
        self._lock = asyncio.Lock()
        LOGGER.debug("[highlight]InMemoryStreamService[/highlight] initialized")

    async def push(
        self,
        request_id: str,
        channel_name: str,
        data: Any,
        session_id: Optional[str] = None
    ) -> None:
        """Push data vào queue của channel được chỉ định.

        Args:
            request_id: Định danh request
            channel_name: Định danh channel
            data: Data cần push (bất kỳ type nào)
            session_id: Định danh session (mặc định "default")
        """
        if not request_id:
            raise ValueError("request_id cannot be None or empty")

        session_id = session_id or self.DEFAULT_SESSION

        try:
            async with self._lock:
                # Đảm bảo queue tồn tại
                if channel_name not in self._queues[session_id][request_id]:
                    self._queues[session_id][request_id][channel_name] = asyncio.Queue()

            # Push vào queue (non-blocking)
            await self._queues[session_id][request_id][channel_name].put(data)

        except Exception as e:
            LOGGER.error("[title]\\[%s][/title] Error pushing data to [muted]%s/%s[/muted]: %s", request_id, session_id, channel_name, e)
            raise

    async def end(self, request_id: str, channel_name: str, session_id: Optional[str] = None) -> None:
        """Báo hiệu kết thúc stream cho channel được chỉ định.

        Args:
            request_id: Định danh request
            channel_name: Định danh channel
            session_id: Định danh session (mặc định "default")
        """
        if not request_id:
            raise ValueError("request_id cannot be None or empty")

        session_id = session_id or self.DEFAULT_SESSION

        try:
            async with self._lock:
                if channel_name not in self._queues[session_id][request_id]:
                    self._queues[session_id][request_id][channel_name] = asyncio.Queue()

            # Push END sentinel để báo hiệu kết thúc
            await self._queues[session_id][request_id][channel_name].put("__END__")
            LOGGER.debug("[title]\\[%s][/title] Pushed END signal to [muted]%s/%s[/muted]", request_id, session_id, channel_name)

        except Exception as e:
            LOGGER.error("[title]\\[%s][/title] Error pushing END signal to [muted]%s/%s[/muted]: %s", request_id, session_id, channel_name, e)
            raise

    async def get(
        self,
        request_id: str,
        channel_name: str,
        session_id: Optional[str] = None,
        timeout: float = 0.01,
        max_idle_time: Optional[float] = None
    ) -> AsyncGenerator[Any, None]:
        """Lấy AsyncGenerator để consume data từ channel được chỉ định.

        Args:
            request_id: Định danh request
            channel_name: Định danh channel
            session_id: Định danh session (mặc định "default")
            timeout: Timeout cho mỗi thao tác queue.get() (đơn vị giây)
            max_idle_time: Thời gian tối đa chờ data mới trước khi dừng.
                          Nếu None, chờ vô thời hạn đến khi nhận END signal.

        Yields:
            Data từ queue cho đến khi nhận END signal hoặc max_idle_time vượt quá
        """
        if not request_id:
            raise ValueError("request_id cannot be None or empty")

        session_id = session_id or self.DEFAULT_SESSION
        last_data_time = time.time()

        try:
            # Đảm bảo queue tồn tại
            async with self._lock:
                if channel_name not in self._queues[session_id][request_id]:
                    self._queues[session_id][request_id][channel_name] = asyncio.Queue()

            queue = self._queues[session_id][request_id][channel_name]

            while True:
                try:
                    # Chờ data với timeout
                    data = await asyncio.wait_for(queue.get(), timeout=timeout)

                    # Kiểm tra END sentinel
                    if data == "__END__":
                        break

                    # Cập nhật thời gian nhận data cuối và yield
                    last_data_time = time.time()
                    yield data

                except asyncio.TimeoutError:
                    # Kiểm tra nếu max_idle_time đã vượt quá
                    if max_idle_time is not None:
                        idle_time = time.time() - last_data_time
                        if idle_time >= max_idle_time:
                            LOGGER.debug(
                                "[title]\\[%s][/title] Max idle time [muted](%ss)[/muted] exceeded for [muted]%s/%s[/muted], stopping",
                                request_id, max_idle_time, session_id, channel_name
                            )
                            break
                    continue

                except Exception as e:
                    LOGGER.warning("[title]\\[%s][/title] Failed to process data from [muted]%s/%s[/muted]: %s", request_id, session_id, channel_name, e)
                    continue

        except Exception as e:
            LOGGER.error("[title]\\[%s][/title] Error consuming from [muted]%s/%s[/muted]: %s", request_id, session_id, channel_name, e)
            raise
        finally:
            # Dọn dẹp queue sau khi consume xong
            async with self._lock:
                if (session_id in self._queues and
                    request_id in self._queues[session_id] and
                    channel_name in self._queues[session_id][request_id]):
                    del self._queues[session_id][request_id][channel_name]

    async def end_request(
        self,
        request_id: str,
        session_id: Optional[str] = None
    ) -> None:
        """Signal that all channels for this request are complete.

        Sends END signal to all active channels for the given request.

        Args:
            request_id: Request identifier
            session_id: Session identifier (default "default")
        """
        if not request_id:
            raise ValueError("request_id cannot be None or empty")

        session_id = session_id or self.DEFAULT_SESSION

        try:
            async with self._lock:
                if session_id in self._queues and request_id in self._queues[session_id]:
                    # Send END to all channels for this request
                    channels = list(self._queues[session_id][request_id].keys())
                    for channel_name in channels:
                        queue = self._queues[session_id][request_id][channel_name]
                        await queue.put("__END__")
                    LOGGER.debug("[title]\\[%s][/title] Sent END signal to all channels for session [muted]%s[/muted]", request_id, session_id)

        except Exception as e:
            LOGGER.error("[title]\\[%s][/title] Error ending request for session [muted]%s[/muted]: %s", request_id, session_id, e)
            raise

    async def get_channels(
        self,
        request_id: str,
        session_id: Optional[str] = None
    ) -> AsyncGenerator[str, None]:
        """Get all channel names for a request.

        Args:
            request_id: Request identifier
            session_id: Session identifier (default "default")

        Yields:
            Channel names
        """
        session_id = session_id or self.DEFAULT_SESSION

        async with self._lock:
            if session_id in self._queues and request_id in self._queues[session_id]:
                for channel_name in self._queues[session_id][request_id].keys():
                    yield channel_name

    def close(self) -> None:
        """Dọn dẹp resource."""
        try:
            self._queues.clear()
            LOGGER.debug("[highlight]InMemoryStreamService[/highlight] closed")
        except Exception as e:
            LOGGER.error("Error closing [highlight]InMemoryStreamService[/highlight]: %s", e)
