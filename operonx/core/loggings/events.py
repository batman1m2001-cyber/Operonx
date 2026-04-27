"""Shared log event templates and plain-text data formatter.

Templates are loaded from log_templates.json — the single source of truth
for log message formats shared between Python and Rust backends.

The plain-text format_data() function mirrors format_log_data() but produces
output WITHOUT Rich markup tags, suitable for cross-backend parity.
"""

import json
import re
from pathlib import Path
from typing import Any

# Load templates once at import time
_TEMPLATES = json.loads((Path(__file__).parent / "log_templates.json").read_text())

# Matches [text] that is NOT a known Rich style tag.
# Rich tags look like [bold], [red], [muted], [/bold], [str], [value], [title], [highlight].
# Template brackets like [request_id_value] or [main] are NOT Rich tags and must be escaped.
_RICH_TAGS = frozenset(
    {
        "bold",
        "dim",
        "italic",
        "underline",
        "strike",
        "reverse",
        "red",
        "green",
        "blue",
        "yellow",
        "cyan",
        "magenta",
        "white",
        "black",
        "muted",
        "str",
        "value",
        "title",
        "highlight",
    }
)
_BRACKET_RE = re.compile(r"\[(/?)([^\]]+)\]")


def _escape_non_rich(text: str) -> str:
    """Escape [brackets] that are not Rich markup tags."""

    def _replace(m: re.Match) -> str:
        slash, content = m.group(1), m.group(2)
        # Keep known Rich tags and closing tags
        if content.strip() in _RICH_TAGS:
            return m.group(0)
        # Keep Rich style expressions (e.g. "bold red", "#ff0000", "dim #8b949e")
        if slash == "/":
            return m.group(0)
        # Escape everything else as literal bracket
        return f"\\[{slash}{content}]"

    return _BRACKET_RE.sub(_replace, text)


def get_template(name: str) -> str:
    """Return the raw template string for a named log event."""
    return _TEMPLATES[name]


def format_event(template_name: str, **kwargs: str) -> str:
    """Format a log event using the shared template.

    The result has non-Rich [brackets] escaped (\\[...]) so it can be
    safely passed to a Rich-enabled logger without losing literal brackets.

    Args:
        template_name: Template name (e.g. "op_done", "op_error").
        **kwargs: Template variables to substitute.

    Returns:
        Formatted log message string (Rich-safe).
    """
    raw = _TEMPLATES[template_name].format(**kwargs)
    return _escape_non_rich(raw)


def format_data(data: Any, max_str: int = 80, max_items: int = 8) -> str:
    """Format data for logging — plain text, NO Rich markup.

    Same logic as format_log_data() but without [muted]/[str]/[value] tags.
    Used by Rust backend and for cross-backend parity verification.

    Args:
        data: Data to format.
        max_str: Max string length before truncation.
        max_items: Max dict/list items before summarizing.

    Returns:
        Plain-text representation of the data.
    """
    if data is None:
        return "None"

    if isinstance(data, str):
        if len(data) > max_str:
            return f"'{data[:max_str]}...'"
        return f"'{data}'"

    if isinstance(data, bool):
        return str(data)

    if isinstance(data, (int, float)):
        return str(data)

    if isinstance(data, bytes):
        return f"<bytes {len(data)}>"

    if isinstance(data, dict):
        if not data:
            return "{}"
        parts = []
        for i, (k, v) in enumerate(data.items()):
            if i >= max_items:
                parts.append(f"+{len(data) - i}")
                break
            if v is None:
                val = "None"
            elif isinstance(v, str):
                val = f"'{v[:50]}...'" if len(v) > 50 else f"'{v}'"
            elif isinstance(v, bool):
                val = str(v)
            elif isinstance(v, (int, float)):
                val = str(v)
            elif isinstance(v, dict):
                val = f"<dict {len(v)}>"
            elif isinstance(v, (list, tuple)):
                val = f"<{type(v).__name__} {len(v)}>"
            else:
                s = str(v)
                val = f"{s[:50]}..." if len(s) > 50 else s
            parts.append(f"{k}={val}")
        return "{" + ", ".join(parts) + "}"

    if isinstance(data, (list, tuple)):
        n = len(data)
        if n == 0:
            return "[]" if isinstance(data, list) else "()"
        if n > max_items:
            return f"<{type(data).__name__} {n}>"
        parts = []
        for v in data:
            if isinstance(v, str):
                parts.append(f"'{v[:30]}...'" if len(v) > 30 else f"'{v}'")
            elif isinstance(v, (bool, int, float)):
                parts.append(str(v))
            else:
                s = str(v)
                parts.append(f"{s[:30]}..." if len(s) > 30 else s)
        bracket = "[]" if isinstance(data, list) else "()"
        return bracket[0] + ", ".join(parts) + bracket[1]

    # Fallback
    s = str(data)
    if len(s) > max_str:
        return f"{s[:max_str]}..."
    return s
