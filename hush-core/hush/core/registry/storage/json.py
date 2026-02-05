"""Storage config dựa trên JSON."""

import json
from pathlib import Path
from typing import Any, Dict, Optional

from hush.core.loggings import LOGGER
from .base import ConfigStorage


class JsonConfigStorage(ConfigStorage):
    """Storage file JSON cho config resource.

    Tất cả config được lưu trong một file JSON duy nhất.

    Cấu trúc file ví dụ:
        {
            "llm:gpt-4": {
                "api_type": "openai",
                "model": "gpt-4",
                "api_key": "sk-xxx"
            },
            "embedding:bge-m3": {
                "api_type": "vllm",
                "base_url": "http://localhost:8000/v1"
            }
        }
    """

    def __init__(self, file_path: Path | str):
        """Khởi tạo JSON storage.

        Args:
            file_path: Đường dẫn đến file config JSON
        """
        self._file_path = Path(file_path)
        self._file_path.parent.mkdir(parents=True, exist_ok=True)

    def _load_file(self) -> Dict[str, Any]:
        """Đọc và parse file JSON."""
        if not self._file_path.exists():
            return {}

        with open(self._file_path, 'r') as f:
            return json.load(f)

    def _save_file(self, data: Dict[str, Any]):
        """Ghi dữ liệu vào file JSON."""
        with open(self._file_path, 'w') as f:
            json.dump(data, f, indent=2)

    def load_one(self, key: str) -> Optional[Dict[str, Any]]:
        """Load một config theo key."""
        try:
            data = self._load_file()
            config_data = data.get(key)

            if not config_data or not isinstance(config_data, dict):
                return None

            return config_data

        except Exception as e:
            LOGGER.error("Không thể load config '%s': %s", key, e)
            return None

    def load_all(self) -> Dict[str, Dict[str, Any]]:
        """Load tất cả config từ file."""
        configs = {}

        try:
            data = self._load_file()
        except json.JSONDecodeError as e:
            LOGGER.error("File JSON không hợp lệ: %s", e)
            return configs

        for key, config_data in data.items():
            if not isinstance(config_data, dict):
                continue

            configs[key] = config_data

        return configs

    def save(self, key: str, config_dict: Dict[str, Any]) -> bool:
        """Lưu config vào file."""
        try:
            data = self._load_file()
            data[key] = config_dict
            self._save_file(data)
            LOGGER.debug("Đã lưu config: %s", key)
            return True
        except Exception as e:
            LOGGER.error("Không thể lưu config '%s': %s", key, e)
            return False

    def remove(self, key: str) -> bool:
        """Xóa config khỏi file."""
        try:
            data = self._load_file()
            if key in data:
                del data[key]
                self._save_file(data)
                LOGGER.debug("Đã xóa config: %s", key)
                return True
            return False
        except Exception as e:
            LOGGER.error("Không thể xóa config '%s': %s", key, e)
            return False

    def close(self):
        """Không làm gì cho file storage."""
        pass
