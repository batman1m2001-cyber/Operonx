"""Storage config dựa trên YAML."""

import os
import re
import warnings
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from operonx.core.loggings import LOGGER

from ..errors import BOOTSTRAP_ENV_PATHS, EnvVarUnsetError, ResourceHubWarning
from .base import ConfigStorage

# Pattern to match ${VAR} or ${VAR:default}
ENV_VAR_PATTERN = re.compile(r"\$\{([^}:]+)(?::([^}]*))?\}")


def _interpolate_env_vars(
    value: Any, missing: Optional[List[Tuple[str, str]]] = None, path: str = ""
) -> Any:
    """Recursively interpolate environment variables in config values.

    Supports:
        ${VAR}          - Required env var (collected into *missing* if not set)
        ${VAR:default}  - Optional env var with default value

    Args:
        value: The value to interpolate.
        missing: If provided, a list that collects tuples of
            ``(var_name, full_path)`` for required vars that aren't set.
        path: Current config path, used in error messages.
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
                if missing is not None:
                    missing.append((var_name, path))
                return match.group(0)  # keep literal; caller will raise

        return ENV_VAR_PATTERN.sub(replace_env_var, value)

    elif isinstance(value, dict):
        return {
            k: _interpolate_env_vars(v, missing, f"{path}.{k}" if path else k)
            for k, v in value.items()
        }

    elif isinstance(value, list):
        return [
            _interpolate_env_vars(item, missing, f"{path}[{i}]")
            for i, item in enumerate(value)
        ]

    return value


def _raise_missing_env_vars(missing: List[Tuple[str, str]], source: str) -> None:
    """Raise a consolidated error listing every unresolved ${VAR} reference.

    Raises ``EnvVarUnsetError`` (a ``RuntimeError`` subclass, so existing
    ``except RuntimeError`` callers continue to work). The message also
    includes any ``.env`` paths that ``operonx.bootstrap()`` searched, so
    the user knows where to add the missing variable.
    """
    # Dedupe vars, keep insertion order of first occurrence for grouping
    var_refs: Dict[str, List[str]] = {}
    for var, path in missing:
        var_refs.setdefault(var, []).append(path)

    lines = ["Missing environment variables referenced in " + source, ""]
    lines.append("  Missing:")
    for var in var_refs:
        lines.append(f"    - {var}")
    lines.append("")
    lines.append("  Referenced by:")
    for var, paths in var_refs.items():
        for p in paths:
            lines.append(f"    {p:40s} = ${{{var}}}")
    lines.append("")
    if BOOTSTRAP_ENV_PATHS:
        lines.append("  .env paths searched by operonx.bootstrap():")
        for p in BOOTSTRAP_ENV_PATHS:
            lines.append(f"    - {p}")
        lines.append("")
    lines.append("  Tips:")
    lines.append("    - Add them to .env (auto-loaded from CWD)")
    lines.append("    - Or set them in your shell environment")
    lines.append("    - For optional vars, use ${VAR:default} syntax")
    raise EnvVarUnsetError("\n".join(lines))


def _scan_unset_env_vars(data: Dict[str, Any]) -> List[Tuple[str, str]]:
    """Walk the loaded config and collect ``(var, key_path)`` for every
    ``${VAR}`` reference whose env var is currently unset.

    Used at construction time to emit warning W2 — does not raise.
    """
    findings: List[Tuple[str, str]] = []

    def visit(value: Any, path: str) -> None:
        if isinstance(value, str):
            for match in ENV_VAR_PATTERN.finditer(value):
                var_name = match.group(1)
                default = match.group(2)
                if default is not None:
                    continue  # has default — never warns
                if os.environ.get(var_name) is None:
                    findings.append((var_name, path))
        elif isinstance(value, dict):
            for k, v in value.items():
                visit(v, f"{path}.{k}" if path else k)
        elif isinstance(value, list):
            for i, item in enumerate(value):
                visit(item, f"{path}[{i}]")

    visit(data, "")
    return findings


class YamlConfigStorage(ConfigStorage):
    """Storage file YAML cho config resource.

    Tất cả config được lưu trong một file YAML duy nhất.
    Hỗ trợ interpolation biến môi trường với syntax ${VAR} hoặc ${VAR:default}.

    Supports both nested and flat YAML formats:

    Nested (preferred)::

        llm:
          gpt-4o:
            api_type: openai
            api_key: ${OPENAI_API_KEY}
          claude-haiku:
            api_type: anthropic

        triton:
          stt:
            url: ${TRITON_URL:localhost:8001}
            model: fastconformer_asr

    Flat (legacy)::

        llm:gpt-4o:
          api_type: openai

    Both resolve to key "llm:gpt-4o".

    Environment variable syntax:
        ${VAR}          - Required, warning if not set
        ${VAR:default}  - Optional with default value
    """

    def __init__(self, file_path: Path | str):
        self._file_path = Path(file_path)
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        self._warn_on_unset_env_vars()

    def _warn_on_unset_env_vars(self) -> None:
        """Scan the YAML once at construction and warn for unset ``${VAR}``.

        Emits one ``ResourceHubWarning`` summarising every unset variable
        and which resource keys reference them. Failing to load (file
        missing, malformed YAML) is silent — that's not this scan's job.
        """
        if not self._file_path.exists():
            return
        try:
            data = self._load_file()
        except Exception:
            return  # malformed file — let load_one/load_all surface it
        findings = _scan_unset_env_vars(data)
        if not findings:
            return
        var_keys: Dict[str, List[str]] = {}
        for var, key_path in findings:
            top = key_path.split(".", 1)[0] if "." in key_path else key_path
            var_keys.setdefault(var, [])
            if top not in var_keys[var]:
                var_keys[var].append(top)
        lines = [
            f"resources.yaml at {self._file_path} references unset environment "
            f"variable(s); resources will fail at resolution unless set before then:"
        ]
        for var, keys in var_keys.items():
            lines.append(f"  - ${{{var}}} (used by: {', '.join(keys)})")
        warnings.warn("\n".join(lines), ResourceHubWarning, stacklevel=3)

    def _load_file(self) -> Dict[str, Any]:
        """Load and flatten YAML. Nested dicts become 'category:name' keys."""
        if not self._file_path.exists():
            return {}

        with open(self._file_path, "r") as f:
            raw = yaml.safe_load(f) or {}

        # Flatten nested structure: {category: {name: config}} → {"category:name": config}
        flat = {}
        for key, value in raw.items():
            if isinstance(value, dict) and ":" not in key:
                # Check if nested: all values are dicts (category → resources)
                # vs flat config dict (has non-dict values like strings, ints)
                all_dicts = all(isinstance(v, dict) for v in value.values())
                if all_dicts and value:
                    # Nested: flatten
                    for name, config in value.items():
                        flat[f"{key}:{name}"] = config
                else:
                    # Flat config dict (legacy key without colon)
                    flat[key] = value
            else:
                # Already flat key (e.g. "llm:gpt-4o")
                flat[key] = value

        return flat

    def _save_file(self, data: Dict[str, Any]):
        """Ghi dữ liệu vào file YAML."""
        with open(self._file_path, "w") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)

    def load_one(self, key: str) -> Optional[Dict[str, Any]]:
        """Load one config by key.

        Env vars in ``${VAR}`` or ``${VAR:default}`` form are interpolated.
        Any required ``${VAR}`` references whose env var is unset raise.
        """
        data = self._load_file()
        config_data = data.get(key)

        if not config_data or not isinstance(config_data, dict):
            return None

        missing: List[Tuple[str, str]] = []
        resolved = _interpolate_env_vars(config_data, missing, path=key)
        if missing:
            _raise_missing_env_vars(missing, source=str(self._file_path))
        return resolved

    def load_all(self) -> Dict[str, Dict[str, Any]]:
        """Load all configs from the YAML file.

        Env vars in ``${VAR}`` or ``${VAR:default}`` form are interpolated.
        Any required ``${VAR}`` references whose env var is unset raise a
        single consolidated error listing all missing vars.
        """
        configs = {}

        try:
            data = self._load_file()
        except yaml.YAMLError as e:
            LOGGER.error("File YAML không hợp lệ: %s", e)
            return configs

        missing: List[Tuple[str, str]] = []
        for key, config_data in data.items():
            if not isinstance(config_data, dict):
                continue
            configs[key] = _interpolate_env_vars(config_data, missing, path=key)

        if missing:
            _raise_missing_env_vars(missing, source=str(self._file_path))

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
