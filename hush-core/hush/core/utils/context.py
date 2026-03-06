"""Quản lý context cho graph hiện tại."""

import contextvars

# Context variable lưu trữ graph đang thực thi
_current_graph = contextvars.ContextVar("current_graph")

# Context variable for streaming output queue (set by engine.stream(), read by Scheduler)
_output_queue = contextvars.ContextVar("output_queue", default=None)


def get_current():
    """Lấy graph hiện tại từ context.

    Returns:
        Graph hiện tại hoặc None nếu không có graph nào đang thực thi.
    """
    try:
        return _current_graph.get()
    except LookupError:
        return None
