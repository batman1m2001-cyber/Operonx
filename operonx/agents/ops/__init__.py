"""Agent ops — thin wrappers over pure logic, kept import-cheap.

Each module here holds ops for one concern. Nothing imports a provider
SDK at module scope; backends resolve lazily so `import operonx.agents`
stays fast.
"""

from __future__ import annotations

from operonx.agents.ops.memory_ops import (
    each_provider,
    memory_write,
    merge_memory,
    provider_prefetch,
)

__all__ = ["each_provider", "provider_prefetch", "merge_memory", "memory_write"]
