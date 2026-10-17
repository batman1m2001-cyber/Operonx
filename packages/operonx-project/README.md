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
uv sync && uv run pytest                     # or: python -m venv .venv && .venv/bin/pip install -e . && pytest
```

## Use

A milestone is started from its requirements and goal; one daemon per
project drives it, and every other command only queues for that daemon.

```bash
operonx project new ../my-service            # an empty repo: docs/brd/ and an empty board
operonx project start --title "first cut" --source brd.docx --repo ../my-service
operonx project daemon --repo ../my-service  # leave it running (or --once)
operonx project status --repo ../my-service  # the board, the cost, what waits for you
operonx project approve m1 --repo ../my-service
operonx project answer m1 C3 a --text "and warn" --repo ../my-service
```

`--mode w1` plans the whole DAG at once (a short goal); `--mode w2`, the
default, plans epics and details cards as the board drains (a real BRD).
The result is the branch `milestone/m1/main`, with `DELIVERY.md` and the
updated `docs/brd/`. Nothing is pushed: a push or a PR is yours to approve.
`operonx-project …` is the same command without core.
