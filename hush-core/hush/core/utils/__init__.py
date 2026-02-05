"""Package các tiện ích dùng chung cho hush-core.

Module này export các tiện ích chính:
    - get_current, _current_graph: Quản lý context graph hiện tại
    - BiMap, BiMapReverse: Cấu trúc dữ liệu ánh xạ hai chiều
    - Param: Định nghĩa parameter cho input/output của node
    - unique_name: Tạo tên duy nhất
    - verify_data: Xác thực dữ liệu
    - raise_error: Raise error với message
    - extract_condition_variables: Trích xuất biến từ condition string
    - fake_chunk_from: Tạo fake chunk response
    - ensure_async: Đảm bảo function tương thích async
    - YamlModel: Base class cho model đọc/ghi YAML
"""

from .context import get_current, _current_graph
from .bimap import BiMap, BiMapReverse
from .common import Param, unique_name, verify_data, raise_error, extract_condition_variables, fake_chunk_from, ensure_async
from .yaml_model import YamlModel

__all__ = [
    "get_current",
    "_current_graph",
    "BiMap",
    "BiMapReverse",
    "Param",
    "unique_name",
    "verify_data",
    "raise_error",
    "extract_condition_variables",
    "fake_chunk_from",
    "ensure_async",
    "YamlModel",
]
