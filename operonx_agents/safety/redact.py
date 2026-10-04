"""Secret redaction, ported from ``operonx.agents.redact``.

Tool output goes two places an agent author does not fully control: into
the model's context, and into whatever the tracer writes to disk or ships
to a hosted backend. A tool that reads `.env`, dumps a config, or prints
a stack trace with a connection string puts a live credential in both.

Where it applies (track3 §4.4.8):

- **Traces and approval requests, by default.** ``Agent(redact=...)``
  (a :class:`Redactor` unless set to ``None``) scrubs every tool call's
  recorded arguments and message, every model call's recorded messages,
  and the arguments an :class:`~operonx_agents.Interruption` shows a
  human. The tool still runs with its real arguments.
- **What the model reads, only when asked**: the :class:`RedactToolOutput`
  hook scrubs each tool's message before it is truncated and handed back.
  Off by default, because over-redaction is also a failure: a model
  reasoning about ``[redacted:…]`` as though it were data is far harder to
  diagnose than a leak.

The patterns match things that are *structurally* credentials: a known
prefix, a labelled assignment, a PEM header. Not "long string of letters".
Regexes cannot recognise every secret; treat this as defence in depth
behind not giving the agent the credential in the first place.
"""

from __future__ import annotations

import operator
import re
from typing import Any, Callable, Dict, Iterable, List, Optional, Pattern, Tuple

from operonx_agents.safety.hooks import Hooks, ToolCall

__all__ = ["DEFAULT_PATTERNS", "PLACEHOLDER", "RedactToolOutput", "Redactor"]

PLACEHOLDER = "[redacted:{kind}]"

# Each entry is (kind, pattern). Patterns capture the *secret* in group 2
# where a label must be preserved, so `api_key = "..."` keeps the name
# and loses the value — the model still learns the setting exists, which
# it often needs, without learning what it is.
DEFAULT_PATTERNS: Tuple[Tuple[str, str], ...] = (
    # Vendor-prefixed keys: unambiguous, no false positives worth worrying about.
    ("openai-key", r"\bsk-[A-Za-z0-9_-]{16,}"),
    ("anthropic-key", r"\bsk-ant-[A-Za-z0-9_-]{16,}"),
    ("github-token", r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    ("slack-token", r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    ("aws-access-key", r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    ("google-key", r"\bAIza[A-Za-z0-9_-]{35}\b"),
    # Structural: a PEM block is never anything else.
    ("private-key", r"-----BEGIN[A-Z ]*PRIVATE KEY-----[\s\S]*?-----END[A-Z ]*PRIVATE KEY-----"),
    ("jwt", r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    # Labelled assignments. The label survives; the value does not.
    (
        "secret-assignment",
        r"(?i)\b((?:api[_-]?key|secret|password|passwd|token|access[_-]?key)"
        r"\s*[:=]\s*)[\"']?([^\s\"'&,;]{8,})[\"']?",
    ),
    ("bearer", r"(?i)\b(authorization\s*:\s*bearer\s+)(\S{8,})"),
    # Credentials embedded in a URL.
    ("url-credentials", r"\b([a-z][a-z0-9+.-]*://[^\s:@/]+:)([^\s@/]+)(?=@)"),
)

#: For each default pattern, a (lower-case) regex its matches cannot do
#: without. A text matching none of them is returned as it is after one
#: search: a trace redacts every message an agent sends, nearly all of
#: which hold no credential, and running eleven patterns over each cost
#: ~0.1 ms per turn of a three-tool agent (``scripts/bench_overhead.py``).
#: A pattern of your own has no trigger, so with one every text is scanned.
_TRIGGERS: Dict[Tuple[str, str], str] = dict(
    zip(
        DEFAULT_PATTERNS,
        (
            r"sk-",
            r"sk-ant-",
            r"gh[pousr]_",
            r"xox[abprs]-",
            r"akia|asia",
            r"aiza",
            r"-----begin",
            r"eyj",
            r"api|secret|passw|token|access",
            r"authorization",
            r"://",
        ),
    )
)


class Redactor:
    """Replaces credential-shaped substrings.

    Args:
        patterns: ``(kind, regex)`` pairs. Defaults to
            :data:`DEFAULT_PATTERNS`. A pattern with capture groups keeps
            group 1 as a visible label and redacts group 2; a pattern with
            no groups redacts its whole match.
        extra: Additional patterns appended to the defaults — the common
            case, since a project usually has one or two internal token
            shapes rather than wanting to rewrite the whole list.
        placeholder: Template receiving ``kind``.

    Example::

        r = Redactor(extra=[("internal-id", r"\\bEMP-\\d{8}\\b")])
        r.scrub('api_key = "sk-abc123..."')   # 'api_key = [redacted:...]'
    """

    __slots__ = ("_compiled", "_gate", "placeholder")

    def __init__(
        self,
        patterns: Optional[Iterable[Tuple[str, str]]] = None,
        extra: Optional[Iterable[Tuple[str, str]]] = None,
        placeholder: str = PLACEHOLDER,
    ) -> None:
        source = list(patterns if patterns is not None else DEFAULT_PATTERNS)
        source.extend(extra or ())
        self.placeholder = placeholder
        self._compiled: List[Tuple[str, Pattern[str], Callable, Optional[Pattern[str]]]] = []
        for kind, expression in source:
            try:
                compiled = re.compile(expression)
            except re.error as exc:
                raise ValueError(f"redaction pattern {kind!r} does not compile: {exc}") from exc
            trigger = _TRIGGERS.get((kind, expression))
            self._compiled.append(
                (
                    kind,
                    compiled,
                    _replacer(placeholder.format(kind=kind)),
                    re.compile(trigger) if trigger is not None else None,
                )
            )
        # One search for any trigger at all, when every pattern has one.
        triggers = [_TRIGGERS.get((k, e)) for k, e in source]
        self._gate = re.compile("|".join(triggers)) if all(triggers) else None

    def scrub(self, text: Any) -> str:
        """Redact a string. Non-strings are stringified first."""
        if text is None:
            return ""
        out = text if isinstance(text, str) else str(text)
        low = out.lower()
        if self._gate is not None and self._gate.search(low) is None:
            return out
        for _, pattern, replace, trigger in self._compiled:
            if trigger is None or trigger.search(low) is not None:
                out = pattern.sub(replace, out)
        return out

    def scrub_message(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """Redact a message's ``content``, leaving the rest untouched.

        Returns a new dict when anything was redacted. Mutating in place
        would rewrite the caller's shared conversation, and a redaction
        applied there would persist into every later turn — which sounds
        desirable until a false positive means the agent can never see the
        real value again.
        """
        if not isinstance(message, dict) or "content" not in message:
            return message
        content = message.get("content")
        scrubbed = self.scrub(content)
        return message if scrubbed is content else {**message, "content": scrubbed}

    def scrub_turn_message(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """A conversation message as a trace may hold it: its ``content``
        and its tool calls' arguments scrubbed, the rest (role, ids, tool
        names: the framework's and the model's bookkeeping) kept. A new
        dict, or ``message`` itself when it holds nothing to redact;
        ``message`` is what the model is sent and is never changed."""
        content = message.get("content")
        scrubbed = self.scrub_data(content) if content else content
        calls = message.get("tool_calls")
        shown = calls
        if calls:
            shown = [
                {**c, "args": args}
                if (args := self.scrub_data(c["args"])) is not c["args"]
                else c
                if isinstance(c, dict) and "args" in c
                else self.scrub_data(c)
                for c in calls
            ]
            if all(map(operator.is_, shown, calls)):
                shown = calls
        if scrubbed is content and shown is calls:
            return message
        out = dict(message)
        if scrubbed is not content:
            out["content"] = scrubbed
        if shown is not calls:
            out["tool_calls"] = shown
        return out

    def scrub_data(self, value: Any, key: Optional[str] = None) -> Any:
        """Redact every string inside a JSON-like value; return a new one.

        For tool **arguments**, which arrive as a structure rather than as
        text. Each string is scrubbed together with the key it sits under,
        as ``"key: value"``: the labelled patterns — ``Authorization:
        Bearer …``, ``token: …`` — match a label and a value, and in a dict
        the label is the key. Scrubbing values alone would let
        ``{"token": "tok_…"}`` through, since only the key says what it
        is. Keys themselves, numbers, booleans and ``None`` are kept.

        Never mutates ``value``: the same dict is what the tool runs with.
        A value holding nothing to redact comes back as itself.
        """
        if key is None and isinstance(value, (dict, list, tuple)) and self.clean(value):
            return value
        if isinstance(value, str):
            if key:
                label = f"{key}: "
                joined = label + value
                scrubbed = self.scrub(joined)
                if scrubbed is joined:
                    return value  # nothing to redact: the same string
                if scrubbed.startswith(label):
                    return scrubbed[len(label) :]
            return self.scrub(value)
        if isinstance(value, dict):
            return {k: self.scrub_data(v, key=str(k)) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            # Items inherit the list's key: ``{"tokens": [...]}`` labels each.
            return [self.scrub_data(item, key=key) for item in value]
        return value

    def __call__(self, values: Any) -> Any:
        """``scrub_data``: a redactor is a record's ``redact`` as it is."""
        return self.scrub_data(values)

    def clean(self, value: Any) -> bool:
        """Whether ``value`` (any data) certainly holds nothing to redact:
        its text, keys included, has none of the default patterns' triggers.
        One search over ``repr(value)``, the fast path for the structures a
        trace records. ``False`` means "look closer", not "found"."""
        if self._gate is None:
            return False
        return self._gate.search(repr(value).lower()) is None

    def found(self, text: Any) -> List[str]:
        """Kinds detected in ``text``, for tests and audit logging."""
        if text is None:
            return []
        candidate = text if isinstance(text, str) else str(text)
        return [kind for kind, pattern, _, _ in self._compiled if pattern.search(candidate)]


def _replacer(replacement: str) -> Callable[["re.Match[str]"], str]:
    def sub(match: "re.Match[str]") -> str:
        # Keep a leading label group so `api_key = ...` stays legible: the
        # model usually needs to know the setting exists, only never what
        # it is.
        if match.lastindex and match.lastindex >= 2:
            return f"{match.group(1)}{replacement}"
        return replacement

    return sub


class RunRedaction:
    """One run's trace redaction, as its records' ``redact``.

    The runner records its tool and model calls as they are, and sets this
    on each record: operonx applies it where the trace leaves the process
    (``child()``'s ``redact``: the run stores, the Local and Langfuse
    consumers), so the run's own loop scrubs nothing. On the loop it cost
    0.03-0.05 ms per turn of a 0.3-0.45 ms budget (``bench_overhead``).

    A conversation repeats its messages in every later model call's record,
    so each message, and each call's arguments, is scrubbed once per run:
    memoised by identity (the run reuses the same objects turn after turn);
    entries keep their object alive, as the trace does anyway.
    """

    __slots__ = ("redactor", "_memo")

    def __init__(self, redactor: Redactor) -> None:
        self.redactor = redactor
        self._memo: Dict[int, Tuple[Any, Any]] = {}

    def __call__(self, values: Dict[str, Any]) -> Dict[str, Any]:
        """A record's inputs or outputs as the trace exports them."""
        if not isinstance(values, dict):
            return self.redactor.scrub_data(values)
        return {key: self._value(key, value) for key, value in values.items()}

    def _value(self, key: str, value: Any) -> Any:
        if _is_message(value):
            return self.scrub_message(value)
        if isinstance(value, list) and value and all(_is_message(m) for m in value):
            return [self.scrub_message(m) for m in value]
        if isinstance(value, (dict, list)):
            return self._once(value, self.redactor.scrub_data)
        return self.redactor.scrub_data(value, key=str(key))

    def _once(self, value: Any, scrub: Callable[[Any], Any]) -> Any:
        hit = self._memo.get(id(value))
        if hit is None or hit[0] is not value:
            hit = self._memo[id(value)] = (value, scrub(value))
        return hit[1]

    def scrub_message(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """A conversation message: its content, and its tool calls'
        arguments (each memoised: the tool's own record shows them too)."""
        return self._once(message, self._message)

    def _message(self, message: Dict[str, Any]) -> Dict[str, Any]:
        calls = message.get("tool_calls")
        if not calls:
            return self.redactor.scrub_turn_message(message)
        content = message.get("content")
        scrubbed = self.redactor.scrub_data(content) if content else content
        shown = [
            {**c, "args": self._once(c["args"], self.redactor.scrub_data)}
            if isinstance(c, dict) and "args" in c
            else self.redactor.scrub_data(c)
            for c in calls
        ]
        return {**message, "content": scrubbed, "tool_calls": shown}


def _is_message(value: Any) -> bool:
    return isinstance(value, dict) and "role" in value


class RedactToolOutput(Hooks):
    """Scrub each tool's message before the model reads it (and before it
    is truncated, so a cut cannot split a secret past the patterns)::

        Agent(..., hooks=[RedactToolOutput()])
    """

    def __init__(self, redactor: Optional[Redactor] = None) -> None:
        self.redactor = redactor or Redactor()

    async def after_tool(self, ctx: Any, call: ToolCall, content: str) -> Optional[str]:
        return self.redactor.scrub(content)
