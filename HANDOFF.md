# Handoff — read this first

Updated 25 Nov 2026 for **1.48.0**.

## State

- `main` is released to PyPI on each version bump (see
  `.claude/skills/publish/`).
- **No catalogued finding is open.** `docs/design/OPEN_FINDINGS.md` is the
  record of the 31 findings of Aug 2026 and the sweeps after it; all are
  fixed in 1.11.0 (P1 and P2 were withdrawn with the package they lived
  in).
- **operonx builds AI products; how people work is theirs.**
  [LEAN_PRODUCT_PLAN](docs/LEAN_PRODUCT_PLAN.md) is DONE: the board, BRD
  engine and cycles left core in 1.47.0 for the private `operonx-project`
  repo, and nothing writes `.operonx/guide/` or AGENTS.md except
  `operonx guide`. Its §9 lists what is still the owner's.
- Tests: `uv run pytest tests/ -m "not integration"` (about 3 minutes,
  offline). `tests/guide/` runs every snippet in `operonx/guide/`.

## Where to start

| Question | File |
|---|---|
| How do I use operonx? | `operonx/guide/` (tested) |
| What changed, and what breaks on upgrade? | `CHANGELOG.md` |
| What does a project look like (`src/`, `app/`, `web/`)? | `operonx/guide/05-project-layout.md` |
| How do contexts, cancellation, errors work? | `docs/architecture/execution-flow.md` |
| What mistakes does this codebase keep making? | `docs/architecture/failure-modes.md` |
| What was broken, and how was it proven? | `docs/design/OPEN_FINDINGS.md` |

## How to fix a bug here

1. Reproduce it with a test that fails, and keep the output.
2. Fix the root cause, not the symptom (see `CLAUDE.md`).
3. The test passes, the full suite and `ruff` are green.
4. Ask what a caller receives when your new code path fails: every
   defect in `failure-modes.md` returned a plausible value instead of
   raising.
