"""Secret redaction for tool output.

Tool output goes two places an agent author does not fully control: into
the model's context, and into whatever the tracer writes to disk or ships
to a hosted backend. A tool that reads `.env`, dumps a config, or prints
a stack trace with a connection string puts a live credential in both.

The design is deliberately conservative, because **over-redaction is also
a failure**. A redactor that eats file paths or ordinary hex produces an
agent that cannot read its own project, and the symptom — the model
reasoning about `[redacted]` as though it were data — is far harder to
diagnose than a leaked key. So the patterns here match things that are
*structurally* credentials: a known prefix, a labelled assignment, a PEM
header. Not "long string of letters".

What this is not: a guarantee. Regexes cannot recognise every secret, and
a determined leak (a key split across two lines, base64 of a base64) gets
through. Treat it as defence in depth behind not giving the agent the
credential in the first place.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Pattern, Tuple

__all__ = ["Redactor", "DEFAULT_PATTERNS", "PLACEHOLDER"]

PLACEHOLDER = "[redacted:{kind}]"

# Each entry is (kind, pattern). Patterns capture the *secret* in group 1
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

    __slots__ = ("_compiled", "placeholder")

    def __init__(
        self,
        patterns: Optional[Iterable[Tuple[str, str]]] = None,
        extra: Optional[Iterable[Tuple[str, str]]] = None,
        placeholder: str = PLACEHOLDER,
    ) -> None:
        source = list(patterns if patterns is not None else DEFAULT_PATTERNS)
        source.extend(extra or ())
        self.placeholder = placeholder
        self._compiled: List[Tuple[str, Pattern[str]]] = []
        for kind, expression in source:
            try:
                self._compiled.append((kind, re.compile(expression)))
            except re.error as exc:
                raise ValueError(f"redaction pattern {kind!r} does not compile: {exc}") from exc

    def scrub(self, text: Any) -> str:
        """Redact a string. Non-strings are stringified first."""
        if text is None:
            return ""
        out = text if isinstance(text, str) else str(text)
        for kind, pattern in self._compiled:
            replacement = self.placeholder.format(kind=kind)

            def _sub(match: "re.Match[str]", _r=replacement) -> str:
                # Keep a leading label group so `api_key = ...` stays
                # legible: the model usually needs to know the setting
                # exists, only never what it is.
                if match.lastindex and match.lastindex >= 2:
                    return f"{match.group(1)}{_r}"
                return _r

            out = pattern.sub(_sub, out)
        return out

    def scrub_message(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """Redact a message's ``content``, leaving the rest untouched.

        Returns a new dict. Mutating in place would rewrite the caller's
        shared conversation cell, and a redaction applied there would
        persist into every later turn — which sounds desirable until a
        false positive means the agent can never see the real value
        again.
        """
        if not isinstance(message, dict) or "content" not in message:
            return message
        return {**message, "content": self.scrub(message.get("content"))}

    def found(self, text: Any) -> List[str]:
        """Kinds detected in ``text``, for tests and audit logging."""
        if text is None:
            return []
        candidate = text if isinstance(text, str) else str(text)
        return [kind for kind, pattern in self._compiled if pattern.search(candidate)]
