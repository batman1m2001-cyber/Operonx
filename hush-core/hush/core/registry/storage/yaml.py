"""Storage config dựa trên YAML."""

import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from hush.core.loggings import LOGGER
from .base import ConfigStorage


# Pattern to match ${VAR} or ${VAR:default}
ENV_VAR_PATTERN = re.compile(r'\$\{([^}:]+)(?::([^}]*))?\}')


def _interpolate_env_vars(value: Any) -> Any:
    """Recursively interpolate environment variables in config values.

    Supports:
        ${VAR}          - Required env var (raises if not set)
        ${VAR:default}  - Optional env var with default value

    Examples:
        api_key: ${OPENAI_API_KEY}
        host: ${REDIS_HOST:localhost}
        port: ${REDIS_PORT:6379}
    """
    if isinstance(value, str):
        def replace_env_var(match):
            var_name = match.group(1)
            default = match.group(2)

            env_value = os.environ.get(var_name)

            if env_value is not None:
                return env_value
            elif default is not None:
                return default
            else:
                LOGGER.warning(
                    "Environment variable '%s' not set and no default provided",
                    var_name
                )
                return match.group(0)  # Return original ${VAR} if not found

        return ENV_VAR_PATTERN.sub(replace_env_var, value)

    elif isinstance(value, dict):
        return {k: _interpolate_env_vars(v) for k, v in value.items()}

    elif isinstance(value, list):
        return [_interpolate_env_vars(item) for item in value]

    return value


class YamlConfigStorage(ConfigStorage):
    """Storage file YAML cho config resource.

    Tất cả config được lưu trong một file YAML duy nhất.
    Hỗ trợ interpolation biến môi trường với syntax ${VAR} hoặc ${VAR:default}.

    Cấu trúc file ví dụ:
        llm:gpt-4:
            api_type: openai
            model: gpt-4
            api_key: ${OPENAI_API_KEY}

        embedding:bge-m3:
            api_type: vllm
            base_url: http://localhost:8000/v1

        redis:default:
            host: ${REDIS_HOST:localhost}
            port: ${REDIS_PORT:6379}

    Environment variable syntax:
        ${VAR}          - Required, warning if not set
        ${VAR:default}  - Optional with default value
    """

    def __init__(self, file_path: Path | str):
        """Khởi tạo YAML storage.

        Args:
            file_path: Đường dẫn đến file config YAML
        """
        self._file_path = Path(file_path)
        self._file_path.parent.mkdir(parents=True, exist_ok=True)

    def _load_file(self) -> Dict[str, Any]:
        """Đọc và parse file YAML."""
        if not self._file_path.exists():
            return {}

        with open(self._file_path, 'r') as f:
            return yaml.safe_load(f) or {}

    def _save_file(self, data: Dict[str, Any]):
        """Ghi dữ liệu vào file YAML."""
        with open(self._file_path, 'w') as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)

    def load_one(self, key: str) -> Optional[Dict[str, Any]]:
        """Load một config theo key.

        Environment variables in format ${VAR} or ${VAR:default} are
        automatically interpolated.
        """
        try:
            data = self._load_file()
            config_data = data.get(key)

            if not config_data or not isinstance(config_data, dict):
                return None

            # Interpolate environment variables
            return _interpolate_env_vars(config_data)

        except Exception as e:
            LOGGER.error("Không thể load config '%s': %s", key, e)
            return None

    def load_all(self) -> Dict[str, Dict[str, Any]]:
        """Load tất cả config từ file.

        Environment variables in format ${VAR} or ${VAR:default} are
        automatically interpolated.
        """
        configs = {}

        try:
            data = self._load_file()
        except yaml.YAMLError as e:
            LOGGER.error("File YAML không hợp lệ: %s", e)
            return configs

        for key, config_data in data.items():
            if not isinstance(config_data, dict):
                continue

            # Interpolate environment variables
            configs[key] = _interpolate_env_vars(config_data)

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
