"""Tool permission policy — decide *before* a call runs.

A tool declares what it is (``destructive``, ``readonly``); the policy
decides what may happen to it in this deployment. Keeping those separate
is the point: the same ``delete_file`` tool is fine unattended in a
sandbox and needs a human in production, and that difference belongs to
the caller, not to the tool's author.

Three outcomes::

    allow   run it
    ask     suspend for a human (InterruptOp)
    deny    refuse without asking anyone

``deny`` is not ``ask`` with an automatic no. Asking a human to approve
something policy has already forbidden trains them to click through, and
their answer would be ignored anyway.

Resolution is most-specific-first::

    rules[name]  →  destructive / readonly default  →  default

Example::

    # Unattended batch run: read freely, never write.
    ToolPolicy(default="deny", readonly="allow")

    # Interactive: ask before anything destructive, but never shell out.
    ToolPolicy(default="allow", destructive="ask", rules={"shell": "deny"})
"""

from __future__ import annotations

from typing import Any, Dict, Literal, Mapping, Optional

__all__ = ["Decision", "ToolPolicy", "DENY_MESSAGE"]

Decision = Literal["allow", "ask", "deny"]

_VALID: frozenset[str] = frozenset({"allow", "ask", "deny"})

DENY_MESSAGE = (
    "Blocked: policy forbids calling {name!r}. This is not a transient "
    "failure and no human will be asked — do not retry it. Use another "
    "approach, or say what you would need."
)


class ToolPolicy:
    """Maps a tool to ``allow`` / ``ask`` / ``deny``.

    Args:
        default: Outcome for a tool no other rule matches.
        rules: Per-tool overrides by name. Beats every other rule.
        destructive: Outcome for tools declaring ``destructive=True``.
            ``None`` falls through to ``default``.
        readonly: Outcome for tools declaring ``readonly=True``. ``None``
            falls through to ``default``.

    Raises:
        ValueError: on an outcome outside ``allow`` / ``ask`` / ``deny``.
            A typo would otherwise fall through to ``default`` and, if
            that default is permissive, silently widen what the agent may
            do — the failure mode a policy exists to prevent.
    """

    __slots__ = ("default", "rules", "destructive", "readonly")

    def __init__(
        self,
        default: Decision = "ask",
        rules: Optional[Mapping[str, Decision]] = None,
        destructive: Optional[Decision] = "ask",
        readonly: Optional[Decision] = "allow",
    ) -> None:
        self.default = self._check(default, "default")
        self.destructive = None if destructive is None else self._check(destructive, "destructive")
        self.readonly = None if readonly is None else self._check(readonly, "readonly")
        self.rules: Dict[str, Decision] = {
            name: self._check(value, f"rules[{name!r}]") for name, value in (rules or {}).items()
        }

    @staticmethod
    def _check(value: Any, where: str) -> Decision:
        if value not in _VALID:
            raise ValueError(
                f"ToolPolicy {where}={value!r} is not one of {sorted(_VALID)}. "
                f"An unrecognised outcome would fall through to the default "
                f"and quietly widen what the agent may do."
            )
        return value  # type: ignore[return-value]

    def decide(self, name: str, meta: Optional[Mapping[str, Any]] = None) -> Decision:
        """Outcome for one tool call.

        An unregistered tool (``meta`` empty) still goes through the
        rules, so ``rules={"shell": "deny"}`` holds even if the tool is
        not loaded — the model gets a policy refusal rather than a
        "no such tool" hint that invites it to look for a way around.
        """
        if name in self.rules:
            return self.rules[name]
        meta = meta or {}
        if meta.get("destructive") and self.destructive is not None:
            return self.destructive
        if meta.get("readonly") and self.readonly is not None:
            return self.readonly
        return self.default

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"ToolPolicy(default={self.default!r}, destructive={self.destructive!r}, "
            f"readonly={self.readonly!r}, rules={self.rules!r})"
        )


#: Used when a caller supplies no policy. Matches the pre-policy
#: behaviour exactly — destructive tools ask, everything else runs — so
#: adding the policy layer changed no existing agent's behaviour.
DEFAULT_POLICY = ToolPolicy(default="allow", destructive="ask", readonly="allow")
