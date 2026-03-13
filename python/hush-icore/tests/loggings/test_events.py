"""Tests for shared log event templates and plain-text data formatter."""

from hush.core.loggings.events import format_data, format_event, get_template

# ── Template loading ─────────────────────────────────────────────────


def test_templates_loaded():
    """All expected templates exist."""
    for name in ["op_done", "op_error", "op_slow", "workflow_start", "workflow_done", "gen_error"]:
        assert get_template(name), f"Missing template: {name}"


# ── format_event ─────────────────────────────────────────────────────
# Note: format_event() escapes non-Rich [brackets] as \[...] for Rich-safe output.


def test_format_event_op_done():
    msg = format_event(
        "op_done",
        request_id="req-1",
        op_type="FUNC",
        full_name="wf.step",
        context="main",
        duration_ms="1.5",
        inputs="{x=5}",
        outputs="{result=10}",
    )
    assert msg == "\\[req-1] FUNC wf.step \\[main] (1.5ms) {x=5} -> {result=10}"


def test_format_event_op_error():
    msg = format_event("op_error", request_id="req-1", name="step", error="division by zero")
    assert msg == "\\[req-1] Error in op step:\ndivision by zero"


def test_format_event_op_slow():
    msg = format_event("op_slow", request_id="req-1", full_name="wf.step", duration_ms="150.3")
    assert msg == "\\[req-1] Slow op wf.step: 150.3ms"


def test_format_event_workflow_start():
    msg = format_event("workflow_start", request_id="req-1", graph_name="my_graph")
    assert msg == "\\[req-1] Running workflow my_graph"


def test_format_event_workflow_done():
    msg = format_event("workflow_done", request_id="req-1", graph_name="my_graph")
    assert msg == "\\[req-1] Workflow my_graph completed"


def test_format_event_gen_error():
    msg = format_event("gen_error", request_id="req-1", name="gen", error="boom")
    assert msg == "\\[req-1] Error in generator op gen:\nboom"


def test_format_event_preserves_rich_tags():
    """Rich markup tags in kwargs should pass through unescaped."""
    msg = format_event(
        "op_done",
        request_id="req-1",
        op_type="FUNC",
        full_name="wf.step",
        context="main",
        duration_ms="1.0",
        inputs="{x=[muted]5[/muted]}",
        outputs="{r=[bold]10[/bold]}",
    )
    # Rich tags preserved, non-Rich brackets escaped
    assert "\\[req-1]" in msg
    assert "\\[main]" in msg
    assert "[muted]" in msg
    assert "[bold]" in msg


# ── format_data: plain text, no Rich markup ──────────────────────────


def test_format_data_none():
    assert format_data(None) == "None"


def test_format_data_bool():
    assert format_data(True) == "True"
    assert format_data(False) == "False"


def test_format_data_number():
    assert format_data(42) == "42"
    assert format_data(3.14) == "3.14"


def test_format_data_string():
    assert format_data("hello") == "'hello'"


def test_format_data_string_truncation():
    long = "a" * 200
    result = format_data(long, max_str=80)
    assert len(result) < 200
    assert result.endswith("...'")
    assert result.startswith("'")


def test_format_data_empty_dict():
    assert format_data({}) == "{}"


def test_format_data_dict():
    result = format_data({"x": 5, "y": "hello"})
    assert "x=5" in result
    assert "y='hello'" in result


def test_format_data_dict_no_rich_tags():
    result = format_data({"key": "value", "num": 42})
    assert "[muted]" not in result
    assert "[str]" not in result
    assert "[value]" not in result
    assert "[title]" not in result
    assert "[highlight]" not in result
    assert "[bold]" not in result


def test_format_data_dict_max_items():
    data = {f"k{i}": i for i in range(20)}
    result = format_data(data, max_items=5)
    assert "+15" in result


def test_format_data_empty_list():
    assert format_data([]) == "[]"


def test_format_data_list():
    assert format_data([1, 2, 3]) == "[1, 2, 3]"


def test_format_data_list_too_long():
    result = format_data(list(range(20)), max_items=8)
    assert result == "<list 20>"


def test_format_data_list_strings():
    result = format_data(["a", "b", "c"])
    assert result == "['a', 'b', 'c']"


def test_format_data_nested_dict():
    result = format_data({"inner": {"a": 1, "b": 2}})
    assert "<dict 2>" in result


def test_format_data_nested_list():
    result = format_data({"items": [1, 2, 3]})
    assert "<list 3>" in result


def test_format_data_bytes():
    assert format_data(b"hello") == "<bytes 5>"
