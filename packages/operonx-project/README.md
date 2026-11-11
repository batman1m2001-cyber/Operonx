# operonx-project

Keeps a project's requirements and where it stands. The requirements, in
any form, become a living BRD with a checklist in `docs/brd/`. The work is
planned in `docs/*_PLAN.md` files with a status block, and `status` reads
both into one document that the studio and its assistant share.

**The guide is
[`operonx_project/guide/01-project.md`](operonx_project/guide/01-project.md)**.
The plan of record is [`docs/LEAN_PROJECT_PLAN.md`](../../docs/LEAN_PROJECT_PLAN.md).

```bash
uv sync && uv run pytest                     # or: python -m venv .venv && .venv/bin/pip install -e . && pytest
```

## Quick start

```bash
operonx init my-service                      # docs/brd/ comes with it (or: operonx project new ../my-service)
operonx project adopt --repo ../shop         # or an existing project: its BRD and a baseline board
operonx project brd register brd.docx --brd ../my-service/docs/brd
operonx project brd check --brd ../my-service/docs/brd
operonx project status --repo ../my-service  # the checklist, the plans, the open questions, the last commits
```

The checklist is `docs/brd/` and the board is `docs/brd/board/`, both in
git; `.operonx/project/` (outside git) is rebuilt from them on a fresh
clone. The studio acts through `operonx project api <json>`, so it needs
`operonx-project` installed where it runs (or `OPERONX_PROJECT_BIN`).
