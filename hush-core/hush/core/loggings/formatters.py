"""Các tiện ích format log."""

from typing import Any

# Indent cho log nhiều dòng
# Rich tự động indent các dòng tiếp theo để căn chỉnh với phần đầu message
LOG_INDENT = "  "  # Indent nhỏ để phân cấp trực quan


def log_break(text: str = "") -> str:
    """Thêm ngắt dòng với indent phù hợp cho log nhiều dòng.

    Sử dụng khi bạn muốn chia log message dài thành nhiều dòng.
    Rich tự động căn chỉnh các dòng tiếp theo với phần đầu message.

    Args:
        text: Nội dung đặt trên dòng mới (tùy chọn)

    Returns:
        Text với tiền tố newline và indent

    Example:
    LOGGER.info(f"Processing request{log_break('user_id: 123')}{log_break('action: update')}")
        # Output:
        # [12/12/25 00:59:32] INFO     [hush.core] Processing request
        #                                           user_id: 123
        #                                           action: update
    """
    return f"\n{LOG_INDENT}{text}"


def format_log_data(data: Any, max_str: int = 80, max_items: int = 8) -> str:
    """Format dữ liệu log một cách đơn giản và nhanh.

    Tối ưu cho performance - tránh recursive calls và xử lý phức tạp.

    Args:
        data: Dữ liệu cần format
        max_str: Độ dài tối đa cho string values
        max_items: Số items tối đa hiển thị

    Returns:
        String đã format cho logging
    """
    if data is None:
        return "[muted]None[/muted]"

    if isinstance(data, str):
        if len(data) > max_str:
            return f"[str]'{data[:max_str]}...'[/str]"
        return f"[str]'{data}'[/str]"

    if isinstance(data, bool):
        return f"[value]{data}[/value]"

    if isinstance(data, (int, float)):
        return f"[value]{data}[/value]"

    if isinstance(data, bytes):
        return f"[muted]<bytes {len(data)}>[/muted]"

    if isinstance(data, dict):
        if not data:
            return "{}"
        parts = []
        for i, (k, v) in enumerate(data.items()):
            if i >= max_items:
                parts.append(f"[muted]+{len(data) - i}[/muted]")
                break
            # Inline format value - không gọi hàm riêng
            if v is None:
                val = "[muted]None[/muted]"
            elif isinstance(v, str):
                val = f"[str]'{v[:50]}...'[/str]" if len(v) > 50 else f"[str]'{v}'[/str]"
            elif isinstance(v, bool):
                val = f"[value]{v}[/value]"
            elif isinstance(v, (int, float)):
                val = f"[value]{v}[/value]"
            elif isinstance(v, dict):
                val = f"[muted]<dict {len(v)}>[/muted]"
            elif isinstance(v, (list, tuple)):
                val = f"[muted]<{type(v).__name__} {len(v)}>[/muted]"
            else:
                s = str(v)
                val = f"[value]{s[:50]}...[/value]" if len(s) > 50 else f"[value]{s}[/value]"
            parts.append(f"{k}={val}")
        return "{" + ", ".join(parts) + "}"

    if isinstance(data, (list, tuple)):
        n = len(data)
        if n == 0:
            return "[]" if isinstance(data, list) else "()"
        if n > max_items:
            return f"[muted]<{type(data).__name__} {n}>[/muted]"
        # List ngắn - hiển thị inline
        parts = []
        for v in data:
            if isinstance(v, str):
                parts.append(f"[str]'{v[:30]}...'[/str]" if len(v) > 30 else f"[str]'{v}'[/str]")
            elif isinstance(v, (bool, int, float)):
                parts.append(f"[value]{v}[/value]")
            else:
                s = str(v)
                parts.append(f"[value]{s[:30]}...[/value]" if len(s) > 30 else f"[value]{s}[/value]")
        bracket = "[]" if isinstance(data, list) else "()"
        return bracket[0] + ", ".join(parts) + bracket[1]

    # Fallback
    s = str(data)
    if len(s) > max_str:
        return f"[value]{s[:max_str]}...[/value]"
    return f"[value]{s}[/value]"
