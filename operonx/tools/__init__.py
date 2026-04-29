"""Operonx CLI tools.

Each module under this package exposes a `main()` callable that's
registered as a `[project.scripts]` entry in `pyproject.toml` so users
get a `operonx-<tool>` shell command after `pip install operonx`.

Currently shipping:
- `operonx-pack` — serialise `@graph` factories to the JSON spec
  consumed by the Rust runtime. See `operonx.tools.pack`.
"""
