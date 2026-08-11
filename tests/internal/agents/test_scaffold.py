"""`operonx.agents` must stay cheap to import.

The package's whole premise is that it is a thin composition layer over
1.0.0 primitives. The failure mode as it grows is a convenience re-export
in ``__init__`` that drags an LLM SDK — or the provider stack — into
every ``import operonx.agents``. Ops resolve their backends lazily via
ResourceHub for exactly this reason; the agent layer inherits the rule.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

pytestmark = pytest.mark.unit

# Import-time offenders: heavy, network-capable, or optional-extra deps.
# A hit here means someone added a top-level import to reach a symbol
# that should have been imported inside the function that uses it.
FORBIDDEN = [
    "openai",
    "anthropic",
    "litellm",
    "onnxruntime",
    "tritonclient",
    "faiss",
    "qdrant_client",
    "psycopg",
    "langfuse",
]


def test_agents_package_imports():
    import operonx.agents

    assert hasattr(operonx.agents, "__all__")


def test_import_pulls_no_heavy_sdk():
    code = (
        "import sys, json; import operonx.agents; "
        f"print(json.dumps([m for m in {FORBIDDEN!r} if m in sys.modules]))"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr

    leaked = __import__("json").loads(proc.stdout.strip().splitlines()[-1])
    assert leaked == [], (
        f"`import operonx.agents` pulled in {leaked}. Move the import "
        "inside the function that needs it."
    )
