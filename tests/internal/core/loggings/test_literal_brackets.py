"""A log message keeps its own square brackets.

Rich uses `[tag]` for markup, so a message carrying literal brackets — any
JSON array — looked exactly like markup and was deleted. Both console
paths lost it, differently:

  * `PlainTextFormatter` ran `strip_markup()`, which does restore `\\[...]`
    escapes but nothing was producing them for an ordinary log call.
  * `ColoredRichHandler` stripped every `[...]` outright below INFO, and
    above it handed the raw message to Rich, which parsed the brackets as
    tags.

Either way this logged `{"transcript": , "action_code": "UNCLEAR"}` — the
transcript silently gone. Found while moving a callbot's call records off
`print`.
"""

import json
import logging

import pytest

from operonx.core.loggings.handlers.console import (
    ColoredRichHandler,
    PlainTextFormatter,
    strip_markup,
)

pytestmark = pytest.mark.unit

PAYLOAD = {"transcript": [{"speaker": "agent", "text": "xin chào"}], "action_code": "UNCLEAR"}


def _record(msg, *args, level=logging.INFO):
    return logging.LogRecord("t", level, __file__, 1, msg, args or None, None)


@pytest.mark.parametrize("level", [logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR])
def test_plain_formatter_keeps_a_json_array(level):
    """The case that motivated this: a CRM record with a transcript."""
    body = json.dumps(PAYLOAD, ensure_ascii=False)
    out = PlainTextFormatter("%(message)s").format(_record("CALL_RESULT %s", body, level=level))
    assert '"transcript": [{"speaker": "agent"' in out, out
    assert json.loads(out.split("CALL_RESULT ", 1)[1]) == PAYLOAD


@pytest.mark.parametrize(
    "msg, expected",
    [
        ("plain [one] end", "plain [one] end"),
        ("[a] [b]", "[a] [b]"),
        ("nested [[two]]", "nested [[two]]"),
        # Nested arrays are ordinary JSON. A regex that consumed whole
        # `[...]` matches never examined the inner bracket, so this used
        # to come out as `[,[3]]`.
        ("[[1,2],[3]]", "[[1,2],[3]]"),
        ("unclosed [oops", "unclosed [oops"),
        ("no brackets at all", "no brackets at all"),
    ],
)
def test_plain_formatter_keeps_literal_brackets(msg, expected):
    assert PlainTextFormatter("%(message)s").format(_record(msg)) == expected


def test_brackets_arriving_through_args_are_kept():
    """The substitution has to see the final text, not the template."""
    out = PlainTextFormatter("%(message)s").format(_record("value=%s", "[three]"))
    assert out == "value=[three]"


def test_rich_markup_is_still_consumed():
    """The escaping must not turn real markup into visible text."""
    assert strip_markup("[bold]hi[/bold]") == "hi"
    assert PlainTextFormatter("%(message)s").format(_record("[bold]hi[/bold]")) == "hi"


def test_nested_json_survives_a_round_trip():
    """The real shape: a payload with an array of arrays."""
    payload = {"runs": [[1, 2], [3]], "t": [{"x": 1}]}
    body = json.dumps(payload)
    out = PlainTextFormatter("%(message)s").format(_record("P %s", body))
    assert json.loads(out.split("P ", 1)[1]) == payload


def test_rich_handler_escapes_rather_than_strips():
    """The Rich path escapes the message instead of gutting it."""
    from operonx.core.loggings.events import _escape_non_rich

    assert ColoredRichHandler is not None  # the patched handler is importable
    msg = "CALL_RESULT " + json.dumps(PAYLOAD, ensure_ascii=False)
    assert '\\[{"speaker"' in _escape_non_rich(msg)
