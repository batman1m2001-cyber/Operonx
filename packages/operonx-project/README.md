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

### Questions, and Telegram

Whatever waits on you (a card's question, a plan to approve, a budget
that ran out) is one question, `Q-nn`; a card's own question is also an
entry in `docs/brd/requirements/open_questions.md`, and your answer is
its resolution. `operonx project pending` lists them; `answer` takes a
card or a `Q-nn`, an option's key or your own words.

With Telegram, the daemon also sends each question with its buttons, and
takes your tap, your reply to the question, or your words when only one
question is open:

```bash
export OPERONX_TELEGRAM_TOKEN=…   # from @BotFather; never logged
operonx project daemon --repo ../my-service --telegram-chat <chat id> --telegram-user <your user id>
```

Only the listed users, in the listed chat, are heard; anyone else is
dropped without a reply. In the chat: `/status`, `/pending`,
`/milestone m1`, `/pause`, `/resume`, `/cancel m1` (asks you to confirm),
`/budget m1 +20`. If Telegram is down, the questions wait and
`operonx project answer` works as ever. A question unanswered for a day is
reminded; after three it takes its safe default, if triage marked one, or
is flagged in `status`.
