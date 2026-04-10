"""TraceFilter — configurable filtering for trace nodes before flushing.

Allows users to control which ops get traced, reducing noise in
Langfuse/OTEL without code changes. Configured via YAML or Python API.

Usage (YAML):
    langfuse:
      default:
        public_key: ...
        trace_filter:
          skip_empty: true
          exclude_ops: [recv_audio, denoise]

Usage (Python):
    tracer = LangfuseTracer(
        resource="langfuse:default",
        trace_filter=TraceFilter(skip_empty=True, exclude_ops=["recv_audio"]),
    )
"""

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

LOGGER = logging.getLogger("hush.tracing")


@dataclass
class TraceFilter:
    """Structured config for controlling which trace nodes are kept.

    Attributes:
        skip_empty: Drop nodes where all output values are None.
        exclude_ops: Drop nodes matching these op names (short or full).
        include_ops: If non-empty, only keep nodes matching these names (whitelist).
        exclude_kinds: Drop nodes by kind (batch, generator, stream_context, etc.).
        protected_types: node_types that are NEVER filtered out.
            Default: ["trace", "generation"] — root trace + LLM calls always kept.
    """

    skip_empty: bool = False
    exclude_ops: List[str] = field(default_factory=list)
    include_ops: List[str] = field(default_factory=list)
    exclude_kinds: List[str] = field(default_factory=list)
    protected_types: List[str] = field(default_factory=lambda: ["trace", "generation"])

    def __post_init__(self) -> None:
        if self.exclude_ops and self.include_ops:
            raise ValueError("Cannot set both exclude_ops and include_ops — use one or the other")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def apply(self, nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Filter nodes and re-parent orphaned children.

        Algorithm:
        1. Identify nodes to remove (respecting protected_types).
        2. Re-parent: children of removed nodes inherit their grandparent.
        3. Cleanup: remove synthetic nodes with no remaining children.
        4. Return filtered list preserving original order.
        """
        if not nodes:
            return nodes

        # Build parent map for re-parenting: trace_key → parent_trace_key
        parent_of: Dict[str, Optional[str]] = {
            n["trace_key"]: n.get("parent_trace_key") for n in nodes
        }

        # Step 1: identify nodes to remove
        remove_keys: Set[str] = set()
        for n in nodes:
            if self._should_remove(n):
                remove_keys.add(n["trace_key"])

        if not remove_keys:
            return nodes

        LOGGER.debug(
            "TraceFilter: removing %d/%d nodes: %s",
            len(remove_keys),
            len(nodes),
            remove_keys,
        )

        # Step 2: re-parent children of removed nodes
        for n in nodes:
            key = n["trace_key"]
            if key in remove_keys:
                continue
            # Walk up the parent chain to find a surviving ancestor
            parent = n.get("parent_trace_key")
            while parent in remove_keys:
                parent = parent_of.get(parent)
            if parent != n.get("parent_trace_key"):
                n["parent_trace_key"] = parent

        # Step 3: build filtered list (excluding removed nodes)
        result = [n for n in nodes if n["trace_key"] not in remove_keys]

        # Step 4: cleanup — remove synthetic nodes with no remaining children
        result = self._remove_empty_synthetics(result)

        return result

    # ------------------------------------------------------------------
    # Filtering logic
    # ------------------------------------------------------------------

    def _should_remove(self, node: Dict[str, Any]) -> bool:
        """Decide whether a single node should be filtered out."""
        # Protected types are NEVER removed
        if node.get("node_type") in self.protected_types:
            return False

        # Synthetic nodes (op_name=None) are not directly filtered by
        # op-name rules — they are cleaned up in the synthetic pass.
        has_op = node.get("op_name") is not None

        # exclude_ops: remove if op matches
        if has_op and self.exclude_ops and self._op_matches(node, self.exclude_ops):
            return True

        # include_ops: remove if op does NOT match (whitelist mode)
        if has_op and self.include_ops and not self._op_matches(node, self.include_ops):
            return True

        # exclude_kinds
        if self.exclude_kinds and node.get("kind") in self.exclude_kinds:
            return True

        # skip_empty: remove if all outputs are None
        if self.skip_empty and has_op and self._is_empty_output(node):
            return True

        return False

    def _op_matches(self, node: Dict[str, Any], op_list: List[str]) -> bool:
        """True if node's display_name or op_name matches any entry."""
        display_name = node.get("display_name", "")
        op_name = node.get("op_name", "")
        for name in op_list:
            if name == display_name or name == op_name:
                return True
        return False

    @staticmethod
    def _is_empty_output(node: Dict[str, Any]) -> bool:
        """True if all output values are None (or outputs dict is empty)."""
        outputs = node.get("outputs")
        if not outputs:
            return True
        return all(v is None for v in outputs.values())

    @staticmethod
    def _remove_empty_synthetics(nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Remove synthetic nodes (stream_context, loop_iter) with no children.

        Cascades: if removing a synthetic leaves its parent synthetic
        childless, remove that too.
        """
        changed = True
        while changed:
            changed = False
            parent_keys: Set[Optional[str]] = {n.get("parent_trace_key") for n in nodes}
            before = len(nodes)
            nodes = [
                n
                for n in nodes
                if not (
                    n.get("kind") in ("stream_context", "loop_iter")
                    and n["trace_key"] not in parent_keys
                )
            ]
            if len(nodes) < before:
                changed = True
        return nodes

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TraceFilter":
        """Parse from a YAML-like dict. Unknown keys are ignored.

        Example:
            TraceFilter.from_dict({
                "skip_empty": True,
                "exclude_ops": ["recv_audio", "denoise"],
            })
        """
        known_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in d.items() if k in known_fields}
        return cls(**filtered)
