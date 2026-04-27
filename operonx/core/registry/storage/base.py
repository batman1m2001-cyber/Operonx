"""Abstract base class cho các backend storage config."""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


class ConfigStorage(ABC):
    """Interface storage backend để lưu trữ config resource.

    Kế thừa class này để thêm các backend storage mới (MongoDB, Redis, v.v.)

    Example:
        class MongoConfigStorage(ConfigStorage):
            def __init__(self, uri: str, database: str):
                self._client = MongoClient(uri)
                self._db = self._client[database]

            def load_one(self, key: str) -> Optional[Dict[str, Any]]:
                return self._db.configs.find_one({"_id": key})

            # ... implement các method khác
    """

    @abstractmethod
    def load_one(self, key: str) -> Optional[Dict[str, Any]]:
        """Load một config theo key.

        Args:
            key: Registry key (ví dụ: 'llm:gpt-4', 'redis:default')

        Returns:
            Dict config thô với field '_class', hoặc None nếu không tìm thấy
        """
        pass

    @abstractmethod
    def load_all(self) -> Dict[str, Dict[str, Any]]:
        """Load tất cả config đã lưu.

        Returns:
            Dictionary ánh xạ registry key sang dict config thô
        """
        pass

    @abstractmethod
    def save(self, key: str, config_dict: Dict[str, Any]) -> bool:
        """Lưu một config.

        Args:
            key: Registry key
            config_dict: Dict config thô (phải bao gồm field '_class')

        Returns:
            True nếu lưu thành công
        """
        pass

    @abstractmethod
    def remove(self, key: str) -> bool:
        """Xóa một config.

        Args:
            key: Registry key cần xóa

        Returns:
            True nếu đã xóa, False nếu không tìm thấy
        """
        pass

    @abstractmethod
    def close(self):
        """Đóng kết nối và dọn dẹp resource."""
        pass
