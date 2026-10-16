# operonx-project

Takes a project's requirements, in any form, to a verified branch. The
requirements become a living BRD with a checklist in `docs/brd/`. The work
runs as cards on a board. Coding agents implement each card, a different
session reviews it, and real tests (some of them held out from the
implementer) decide whether it passes. When a decision needs the owner, the
card's run parks and resumes when the answer comes, over Telegram, the CLI or
the studio.

Status: in development. The plan of record is
[`docs/PROJECT_ENGINE_PLAN.md`](../../docs/PROJECT_ENGINE_PLAN.md).

```bash
uv sync && uv run pytest
```
