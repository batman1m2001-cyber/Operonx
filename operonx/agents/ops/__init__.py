"""Agent ops — thin wrappers over pure logic, kept import-cheap.

Each module here holds ops for one concern. Nothing imports a provider
SDK at module scope; backends resolve lazily so `import operonx.agents`
stays fast.
"""

from __future__ import annotations

from operonx.agents.ops.compact_ops import (
    apply_compaction,
    count_tokens,
    estimate_tokens,
    plan_compaction,
    unmatched_tool_calls,
)
from operonx.agents.ops.memory_ops import (
    each_provider,
    memory_write,
    merge_memory,
    provider_prefetch,
)
from operonx.agents.ops.prompt_ops import (
    apply_cache_control,
    assemble_api_messages,
    build_system_prompt,
    prefix_is_stable,
)

__all__ = [
    "each_provider",
    "provider_prefetch",
    "merge_memory",
    "memory_write",
    "count_tokens",
    "estimate_tokens",
    "plan_compaction",
    "apply_compaction",
    "unmatched_tool_calls",
    "build_system_prompt",
    "assemble_api_messages",
    "apply_cache_control",
    "prefix_is_stable",
]
