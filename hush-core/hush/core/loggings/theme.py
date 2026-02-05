"""Theme và highlighter cho logging."""

from rich.theme import Theme


# Theme logging sạch - tối ưu cho khả năng đọc
# INFO là trung tính (mặc định), chỉ các vấn đề mới nổi bật
LOGGING_THEME = Theme({
    # Log levels
    "logging.level.debug": "dim #6e7681",       # Xám mờ
    "logging.level.info": "#a8c7fa",            # Xanh dương nhạt (light blue)
    "logging.level.warning": "#f0a875",         # Nâu cam sáng hơn
    "logging.level.error": "#ff7b72",           # Đỏ sáng hơn
    "logging.level.critical": "bold reverse #b81c1c",  # Trắng trên nền đỏ đậm

    # Components
    "log.time": "dim white",                    # Timestamp mờ
    "log.message": "#d0e2f7",                    # Light blue vừa phải
    "log.path": "#6e7681",                      # Path xám

    # Inline markup (mono tone)
    "muted": "#8b949e",                         # Xám - metadata/secondary
    "highlight": "bright_blue",                 # Xanh dương - nhấn mạnh
    "bold": "bold white",                       # Trắng đậm - quan trọng
    "title": "#93c5fd",                          # Light blue-cyan - title/request_id/module
    "value": "#c9d1d9",                         # Light gray - data values
    "str": "#a5f3fc",                           # Light cyan - string values
})
