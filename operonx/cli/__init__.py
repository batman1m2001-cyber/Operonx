"""Operonx command-line entry points.

Renamed from ``operonx.tools`` in 1.2.0 — ``tools`` now reads as *agent
tools* (the ``@tool``-decorated callables an LLM invokes), so the CLI
namespace gave up the name to avoid a permanent clash with
``operonx.agents``.

Each module here exposes a ``main()`` registered as a
``[project.scripts]`` entry in ``pyproject.toml``, so users get an
``operonx-<name>`` shell command after ``pip install operonx``.

Currently shipping:

- ``operonx-pack`` — serialise ``@graph`` factories to the JSON spec
  consumed by the Rust runtime. See :mod:`operonx.cli.pack`.

There is deliberately **no umbrella ``operonx`` command**. Operonx is a
library; the only thing it needs a shell for is handing graph specs to
the Rust runtime. A dispatcher with nothing to dispatch to is API
surface we would owe compatibility on forever. (An ``operonx =
"operonx.cli:main"`` script entry did exist in ``pyproject.toml`` from
the April 2026 Hush→Operon migration through 1.1.0, pointing at a
scaffolding CLI that was deleted in that same migration. It never
resolved. Removed in 1.2.0.)
"""
