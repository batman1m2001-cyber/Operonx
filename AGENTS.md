# AGENTS.md — operonx-kb

A package on **operonx** (editable from `../Operon`). Read `PLAN.md` before changing anything: it
holds the decisions (no `docling` dependency, docling-parse behind `PdfBackend`, no shims for
upstream gaps) and each phase's gate.

## Before you write code

1. Read the operonx guide: `../Operon/operonx/guide/` (README, then 01–05). Do not write an operonx
   API from memory.
2. `operonx_kb/ops/*` holds op logic; `operonx_kb/graphs/*` only wires (no logic, no I/O, no Python
   `if`; branch with `if_`).
3. Ops receive resource **keys** (`kb_catalog:main`), never objects: inputs and outputs are traced
   as JSON. Heavy values (trees, chunk lists) are excluded from traces with `@op(exclude=...)`.

## Rules

- The span invariant (`canonical[e.span] == e.text`) is checked when a version is built and again
  before commit. A violation is a bug: fix the producer, never relax the check.
- Never `print()`; log with `from operonx.core.loggings import LOGGER`.
- An op that raises does not raise: graph tests assert `"$errors" not in out`.
- Every behaviour change ships with a test. Golden snapshots change only with `--update-golden`
  and a reviewed diff.
- `uv run pytest -q` must stay green. Never call a real model in tests: use
  `operonx_kb.testing.fakes`.
