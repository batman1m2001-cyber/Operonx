# Plan: operonx-agents and operonx-kb move into this repo

**Why.** Both packages are ready for PyPI (agents 0.1.1, kb 0.2.1, tagged), but
the PyPI token is a secret of this repo only and GitHub secrets cannot be read
back. This repo already publishes `operonx` from `main`; the two packages move
here and publish through the same workflow.

**Shape.** `packages/operonx-agents/` and `packages/operonx-kb/`, each its own
distribution (own `pyproject.toml`, version, tests, lock). `operonx` itself is
unchanged: its wheel and sdist include only `operonx/`, and the lint and test
workflows only cover `operonx/ tests/ examples/python/`, so nothing under
`packages/` leaks into it.

## Decisions

| # | Decision |
|---|---|
| D1 | `git subtree add` from each repo's remote default branch (agents `main`, kb `origin/master`): full history kept, no rewrite. |
| D2 | Each package depends on `operonx` from PyPI (`>=1.16`); for development `[tool.uv.sources] operonx = { path = "../..", editable = true }`, so a change to operonx and a package can land in one PR. |
| D3 | One publish workflow. Each distribution has its own version check (its `pyproject.toml` against the previous commit), build (`uv build` in its directory) and publish step, with the same `PYPI_API_TOKEN`. Tags: `vX.Y.Z` for operonx (unchanged), `operonx-agents-vX.Y.Z`, `operonx-kb-vX.Y.Z`. |
| D4 | Each package keeps its own test suite, run by a CI job per package (path-filtered to `packages/<name>/**` and `operonx/**`). The packages' own `.github/` folders are deleted; their jobs move into this repo's workflows. |
| D5 | The kb sdist is limited to `operonx_kb`, README, LICENSE, `pyproject.toml` (the repo carries ~10 MB of bench screenshots and datasets). kb gets an Apache-2.0 LICENSE like agents. |
| D6 | The old repos are archived with a README pointing here; their issues and tags stay where they are. |
| D7 | Both packages are already public-safe: a history scan found no secrets or env files; the only key-like strings are fake fixtures in agents `tests/test_redact.py`. |

## Steps

1. **Import.** Subtree-add both packages (D1). Remove the stale untracked
   `packages/operonx-{code,project,studio}` leftovers from the working tree
   (not tracked, not in git).
2. **Packaging.** uv source paths to `../..` (D2), kb sdist and LICENSE (D5).
   Check: `uv build` in each package; `twine check`; a clean install of each wheel
   against PyPI `operonx==1.16.0` imports `operonx.agents` / `operonx.kb`.
3. **Tests.** Each package's suite passes from its new directory (`uv run pytest`
   inside `packages/<name>`), and operonx's own suite is unaffected.
4. **CI.** Publish workflow per D3; test jobs per D4. Repo docs (`CLAUDE.md`,
   `AGENTS.md`, README) mention the layout: where each package lives and how it
   is released.
5. **Merge.** Merging publishes `operonx-agents` 0.1.1 and `operonx-kb` 0.2.1
   (both names are free on PyPI). If the token is scoped to the `operonx` project
   only, PyPI refuses to create the new projects: then the user adds one
   account-wide token (or a trusted publisher) and the workflow is re-run with
   `force`.
6. **Archive** the old repos (D6).
7. **Follow-up: meeting-prep** moves to `operonx-agents` from PyPI and drops the
   `<1.16` pins (`brd`: one MCP import; `meeting-prep-operonx`: `build_react_agent`
   → Agent/Runner).

## Risk

- PyPI token scope (step 5): known only at the first publish.
- Worktrees: `../..` resolves inside a worktree of this repo too, which fixes the
  old "no worktrees" limit of the agents repo.
