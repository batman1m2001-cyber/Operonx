"""Create a new operonx project that already satisfies the conventions.

A generator that emitted a project failing its own linter would be worse
than none, so the acceptance test for everything here is the one a user
would run next:

    operonx-lint --build .      # C1-C7, and the graph builds offline
    operonx-extract .           # the IR is producible
    operonx-studio .            # it renders

The layout matches the tutorial examples rather than inventing a new one:
``pyproject.toml`` for uv, a single module holding the graph, and
``operonx.toml`` declaring what a tool may load. There is no
``requirements.txt`` — uv and every example use ``pyproject.toml``.
"""

from __future__ import annotations

from pathlib import Path
from typing import List

__all__ = ["scaffold", "ScaffoldError", "OPERONX_PIN"]

# Pinned at the floor the tutorial uses, not `>=1.0`: 1.0.0 was a breaking
# release, so a lower floor would let a fresh project resolve to an API the
# generated code does not speak.
OPERONX_PIN = "1.3.0"


class ScaffoldError(Exception):
    """The target directory is unusable."""


_PYPROJECT = """\
[project]
name = "{dist}"
version = "0.1.0"
description = "An operonx workflow."
requires-python = ">=3.10"
dependencies = [
    "operonx{extras}>={pin}",
]
"""

_MANIFEST = """\
# Declares the graphs a tool may load, and where resources come from.
# `operonx-lint`, `operonx-extract` and `operonx-studio` all read this.

[project]
name = "{name}"
description = "An operonx workflow."
{resources}
[[graph]]
name  = "flow"
entry = "workflow:flow"
"""

_RESOURCES_BLOCK = """
[resources]
overlay = "resources.yaml"
"""

_WORKFLOW_PLAIN = '''\
"""The project\'s workflow.

Two rules keep this loadable by a UI, and both are checked by
``operonx-lint``:

* every op is assigned to a plain variable — that name *is* the node\'s
  identity, so layout, comments and diffs all key on it;
* wiring is declarative — no loop builds or connects ops, because a diagram
  has no stable node to map a generated one back to.
"""

from operonx.core import END, PARENT, START, graph, op


@op
def normalise(text: str):
    """Trim and collapse whitespace."""
    return {{"cleaned": " ".join((text or "").split())}}


@op
def summarise(cleaned: str):
    """Report what came through."""
    return {{"summary": f"{{len(cleaned)}} chars: {{cleaned[:40]}}"}}


@graph
def flow(text):
    clean = normalise(text=text)
    report = summarise(cleaned=clean["cleaned"])
    report["summary"] >> PARENT["summary"]
    START >> clean >> report >> END
'''

_WORKFLOW_LLM = '''\
"""The project\'s workflow.

Two rules keep this loadable by a UI, and both are checked by
``operonx-lint``:

* every op is assigned to a plain variable — that name *is* the node\'s
  identity, so layout, comments and diffs all key on it;
* ``resource=`` is a literal string, so a tool can tell which resource this
  op uses without running the code.
"""

from operonx.core import END, PARENT, START, graph, op
from operonx.providers import LLMOp


@op
def normalise(text: str):
    """Trim and collapse whitespace."""
    return {{"cleaned": " ".join((text or "").split())}}


@graph
def flow(text):
    clean = normalise(text=text)
    answer = LLMOp.of(
        resource="gpt-4o-mini",
        prompt={{"system": "Answer in one sentence.", "user": "{{question}}"}},
        question=clean["cleaned"],
    )
    answer["content"] >> PARENT["answer"]
    START >> clean >> answer >> END
'''

_RESOURCES = """\
# Resource keys this project references by name. `${{VAR}}` is required,
# `${{VAR:default}}` optional — the env contract is derived from these, so
# there is no second list to keep in step.

llm:gpt-4o-mini:
  api_type: openai
  api_key: ${{OPENAI_API_KEY}}
  base_url: https://api.openai.com/v1
  model: gpt-4o-mini
"""

_ENV_EXAMPLE = """\
# Copy to .env and fill in. Values are never read by the studio — only
# whether a name is set.
OPENAI_API_KEY=sk-...
"""

_README = """\
# {name}

```bash
uv sync
operonx-lint --build .     # conventions, and the graph builds offline
operonx-studio . --serve   # graph, resources, env and deps in a browser
```

`workflow.py` holds the graph; `operonx.toml` declares what a tool may load.
"""


def scaffold(root: Path | str, name: str | None = None, *, with_llm: bool = False) -> List[Path]:
    """Write a new project into *root*. Returns the files created.

    Raises:
        ScaffoldError: the directory already holds a project, or a file
            would be overwritten. Never clobbers — a scaffolder that
            silently replaced someone's work would be unusable.
    """
    root = Path(root)
    name = name or root.resolve().name
    dist = name.replace("_", "-").replace(" ", "-").lower()

    files = {
        "pyproject.toml": _PYPROJECT.format(
            dist=dist, pin=OPERONX_PIN, extras="[openai]" if with_llm else ""
        ),
        "operonx.toml": _MANIFEST.format(name=name, resources=_RESOURCES_BLOCK if with_llm else ""),
        "workflow.py": (_WORKFLOW_LLM if with_llm else _WORKFLOW_PLAIN).format(),
        "README.md": _README.format(name=name),
    }
    if with_llm:
        files["resources.yaml"] = _RESOURCES.format()
        files[".env.example"] = _ENV_EXAMPLE.format()

    existing = [n for n in files if (root / n).exists()]
    if existing:
        raise ScaffoldError(
            f"{root} already contains {', '.join(sorted(existing))} — refusing to overwrite"
        )

    root.mkdir(parents=True, exist_ok=True)
    written = []
    for filename, content in sorted(files.items()):
        path = root / filename
        path.write_text(content, encoding="utf-8")
        written.append(path)
    return written
