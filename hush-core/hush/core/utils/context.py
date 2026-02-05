"""Quản lý context cho graph hiện tại."""

import contextvars
from typing import Optional

# Context variable lưu trữ graph đang thực thi
_current_graph = contextvars.ContextVar("current_graph")


def get_current():
    """Lấy graph hiện tại từ context.

    Returns:
        Graph hiện tại hoặc None nếu không có graph nào đang thực thi.
    """
    try:
        return _current_graph.get()
    except LookupError:
        return None
