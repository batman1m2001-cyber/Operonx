"""Các hàm tiện ích dùng chung cho hush-core."""

import asyncio
import inspect
import linecache
import re
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Type


def _infer_type(value: Any) -> Type:
    """Suy luận type từ giá trị literal (chỉ basic types)."""
    if value is None:
        return Any
    if isinstance(value, str):
        return str
    if isinstance(value, bool):  # bool trước int vì bool là subclass của int
        return bool
    if isinstance(value, int):
        return int
    if isinstance(value, float):
        return float
    if isinstance(value, list):
        return list
    if isinstance(value, dict):
        return dict
    return Any


@dataclass
class Param:
    """Định nghĩa parameter cho input/output của node.

    Attributes:
        type: Kiểu dữ liệu của parameter (str, int, list, etc.)
              Tự động infer từ value nếu không được chỉ định.
        required: Có bắt buộc hay không
        default: Giá trị mặc định nếu không được cung cấp
        description: Mô tả parameter
        value: Giá trị hoặc tham chiếu (Ref | literal | None)

    Example:
        inputs = {
            "query": Param(str, required=True, description="Search query", value=other_node["result"]),
            "limit": Param(default=10, description="Max results"),  # type inferred as int
        }
    """
    type: Type = None
    required: bool = False
    default: Any = None
    description: str = ""
    value: Any = None  # Ref | literal | None

    def __post_init__(self):
        """Auto-infer type từ value hoặc default nếu type chưa được chỉ định."""
        if self.type is None:
            # Thử infer từ value trước, sau đó từ default
            if self.value is not None:
                self.type = _infer_type(self.value)
            elif self.default is not None:
                self.type = _infer_type(self.default)
            else:
                self.type = Any


def unique_name() -> str:
    """Tạo tên duy nhất sử dụng UUID."""
    return uuid.uuid4().hex[:8]


def verify_data(data: Any) -> bool:
    """Xác thực dữ liệu hợp lệ (không chứa các type không hợp lệ)."""
    # Validation cơ bản - có thể mở rộng
    return True


def raise_error(message: str) -> None:
    """Raise ValueError với message được cung cấp."""
    raise ValueError(message)


def extract_condition_variables(condition: str) -> Dict[str, str]:
    """Trích xuất tên biến và type suy luận từ condition string.

    Args:
        condition: Chuỗi condition như "a > 10 and b == 'hello'"

    Returns:
        Dict ánh xạ tên biến sang type suy luận
    """
    # Pattern để match tên biến (identifier không theo sau bởi dấu ngoặc mở)
    # Loại trừ Python keyword và built-in name
    keywords = {'and', 'or', 'not', 'in', 'is', 'True', 'False', 'None', 'if', 'else'}

    # Tìm tất cả identifier
    identifiers = re.findall(r'\b([a-zA-Z_][a-zA-Z0-9_]*)\b', condition)

    variables = {}
    for var in identifiers:
        if var not in keywords and var not in variables:
            # Thử suy luận type từ context
            variables[var] = "Any"

    return variables


def fake_chunk_from(content: str, model: str = "default") -> Dict[str, Any]:
    """Tạo fake chunk response cho xử lý lỗi."""
    return {
        "choices": [{
            "delta": {
                "content": content
            }
        }],
        "model": model
    }


def ensure_async(func: Callable) -> Callable:
    """Đảm bảo function tương thích với async.

    Nếu function đã là async, trả về nguyên vẹn.
    Nếu là synchronous, wrap để chạy trong thread pool executor.

    Args:
        func: Function cần làm cho tương thích async

    Returns:
        Async function có thể await

    Example:
        def sync_func(x):
            return x * 2

        async def async_func(x):
            return x * 2

        # Cả hai giờ có thể await:
        func = ensure_async(sync_func)
        result = await func(10)
    """
    if asyncio.iscoroutinefunction(func):
        return func

    async def async_wrapper(*args, **kwargs):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: func(*args, **kwargs))

    return async_wrapper


def _auto_name() -> Optional[str]:
    """Extract variable name by walking up frames past all __init__ methods.

    Unlike _caller_name(depth), this automatically finds the
    correct frame regardless of inheritance depth.
    """
    frame = inspect.currentframe()
    try:
        frame = frame.f_back  # skip this function
        while frame and (frame.f_code.co_name == '__init__'
                         or getattr(frame.f_code, '_skip_auto_name', False)
                         or frame.f_locals.get('_skip_auto_name')):
            frame = frame.f_back
        if frame is None:
            return None
        filename = frame.f_code.co_filename
        lineno = frame.f_lineno
        for offset in range(21):
            line = linecache.getline(filename, lineno - offset).strip()
            if '=' in line:
                candidate = line.split('=')[0].strip()
                if candidate.isidentifier():
                    return candidate
        return None
    finally:
        del frame


