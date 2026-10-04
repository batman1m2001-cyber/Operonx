"""Tracebacks that start where the user's code is.

An op that raises is caught in ``BaseOp.run``, so every traceback of an op
failure begins with two or three frames of operonx's own machinery
(``BaseOp.run`` → ``_exec_core`` → the op's body). Those frames are the
same for every failure and say nothing about this one; in ``"$errors"`` they
are what a reader has to skip to find the line that raised.

:func:`user_traceback` drops them. The full text, operonx frames included,
stays in the trace node and the op's ``error`` cell, which is where someone
debugging operonx itself looks.
"""

from __future__ import annotations

import traceback
from pathlib import Path

__all__ = ["user_traceback"]

#: Frames under this directory are operonx's, not the user's.
_OPERONX_DIR = str(Path(__file__).resolve().parents[2]) + "/"


def _is_operonx_frame(frame: traceback.FrameSummary) -> bool:
    return str(Path(frame.filename).resolve()).startswith(_OPERONX_DIR)


def user_traceback(exc: BaseException) -> str:
    """*exc* formatted like ``traceback.format_exception``, minus operonx frames.

    Chained exceptions (``raise ... from``, or one raised while handling
    another) are kept and trimmed the same way. An exception raised by
    operonx itself — a missing input, a prompt that does not render — has
    no user frame left, and formats as its last line alone:
    ``"TypeError: enrich() missing 1 required positional argument"``.
    """
    te = traceback.TracebackException.from_exception(exc)
    seen = set()
    link = te
    while link is not None and id(link) not in seen:
        seen.add(id(link))
        link.stack = traceback.StackSummary.from_list(
            [f for f in link.stack if not _is_operonx_frame(f)]
        )
        link = link.__cause__ or (None if link.__suppress_context__ else link.__context__)
    return "".join(te.format())
