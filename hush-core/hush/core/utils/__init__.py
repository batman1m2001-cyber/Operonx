"""Package các tiện ích dùng chung cho hush-core.

Module này export các tiện ích chính:
    - auto_name, register_skip, unique_name: Auto-naming cho nodes
    - get_current, _current_graph: Quản lý context graph hiện tại
    - BiMap, BiMapReverse: Cấu trúc dữ liệu ánh xạ hai chiều
    - Param: Định nghĩa parameter cho input/output của node
    - verify_data: Xác thực dữ liệu
    - raise_error: Raise error với message
    - extract_condition_variables: Trích xuất biến từ condition string
    - fake_chunk_from: Tạo fake chunk response
    - YamlModel: Base class cho model đọc/ghi YAML
"""

from .auto_name import auto_name, register_skip, unique_name
from .bimap import BiMap, BiMapReverse
from .common import (
    Param,
    extract_condition_variables,
    fake_chunk_from,
    raise_error,
    verify_data,
)
from .context import _current_graph, get_current
from .yaml_model import YamlModel

__all__ = [
    "auto_name",
    "register_skip",
    "unique_name",
    "get_current",
    "_current_graph",
    "BiMap",
    "BiMapReverse",
    "Param",
    "verify_data",
    "raise_error",
    "extract_condition_variables",
    "fake_chunk_from",
    "YamlModel",
]
