from abc import ABC, abstractmethod
from typing import AsyncGenerator, Any, Optional


class BaseStreamingService(ABC):
    """Abstract base class cho các streaming service.

    Cung cấp interface thống nhất cho nhiều backend khác nhau:
    in-memory, Redis, Kafka, RabbitMQ, NATS, Pulsar, v.v.

    Hỗ trợ stream mọi loại data: dict, chunk, event, hoặc custom object.
    """

    @abstractmethod
    async def push(
        self,
        request_id: str,
        channel_name: str,
        data: Any,
        session_id: Optional[str] = None
    ) -> None:
        """Push data vào channel được chỉ định.

        Args:
            request_id: Định danh request
            channel_name: Định danh channel
            data: Data cần push (có thể là dict, object, hoặc bất kỳ type serializable nào)
            session_id: Định danh session (optional) cho multi-tenant
        """
        pass

    @abstractmethod
    async def end(
        self,
        request_id: str,
        channel_name: str,
        session_id: Optional[str] = None
    ) -> None:
        """Báo hiệu kết thúc stream cho channel được chỉ định.

        Push sentinel value để thông báo không còn data nào được gửi.
        Consumer sẽ dừng iteration khi nhận được tín hiệu này.

        Args:
            request_id: Định danh request
            channel_name: Định danh channel
            session_id: Định danh session (optional)
        """
        pass

    @abstractmethod
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
            session_id: Định danh session (optional)
            timeout: Timeout cho mỗi thao tác queue.get() (đơn vị giây)
            max_idle_time: Thời gian tối đa chờ data mới trước khi dừng.
                          Nếu None, chờ vô thời hạn đến khi nhận END signal.
                          Hữu ích cho timeout-based termination.

        Yields:
            Các item data từ stream cho đến khi nhận END signal hoặc timeout

        Example:
            # Chờ END signal (mặc định)
            async for data in stream.get(req_id, channel):
                process(data)

            # Dừng sau 5 giây không có data
            async for data in stream.get(req_id, channel, max_idle_time=5.0):
                process(data)
        """
        pass

    @abstractmethod
    def close(self) -> None:
        """Đóng kết nối và dọn dẹp resource."""
        pass
