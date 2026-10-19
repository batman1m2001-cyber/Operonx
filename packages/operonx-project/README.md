# operonx-project

Takes a project's requirements, in any form, to a verified branch. The
requirements become a living BRD with a checklist in `docs/brd/`. The work
runs as cards on a board. Coding agents implement each card, a different
session reviews it, and real tests (some of them held out from the
implementer) decide whether it passes. When a decision needs the owner, the
card's run parks and resumes when the answer comes, over Telegram, the CLI or
the studio.

Status: in development. The plans of record are
[`docs/PROJECT_BUILDER_PLAN.md`](../../docs/PROJECT_BUILDER_PLAN.md) (the
board in the BRD, take/submit, the studio as the console) and
[`docs/PROJECT_ENGINE_PLAN.md`](../../docs/PROJECT_ENGINE_PLAN.md) (the
engine). **The guide is
[`operonx_project/guide/01-project.md`](operonx_project/guide/01-project.md)**;
it covers everything below, in detail.

```bash
uv sync && uv run pytest                     # or: python -m venv .venv && .venv/bin/pip install -e . && pytest
```

## Quick start

```bash
operonx init my-service                      # docs/brd/ comes with it (or: operonx project new ../my-service)
operonx project adopt --repo ../shop         # or an existing project: its BRD and a baseline board
operonx project start --title "first cut" --source brd.docx --repo ../my-service
operonx project daemon --repo ../my-service  # leave it running (the studio's Run does the same)
operonx project status --repo ../my-service  # the board, the cost, what waits for you
operonx project approve m1 --repo ../my-service
```

The checklist is `docs/brd/` and the board is `docs/brd/board/`, both in
git. The engine's runtime state, `.operonx/project/`, is rebuilt from them
on a fresh clone. The result of a milestone is the branch
`milestone/m1/main`, with `DELIVERY.md`. Nothing is pushed. To work a card
yourself, use `take` and then `submit`. `operonx-project …` is the same
command without core.

### Questions, and Telegram

Whatever waits on you (a card's question, a plan to approve, a budget
that ran out) is one question, `Q-nn`: `operonx project pending` lists
them, and `answer` takes an option's key or your own words. With
Telegram, the daemon also sends each question with its buttons:

```bash
export OPERONX_TELEGRAM_TOKEN=…   # from @BotFather; never logged
operonx project daemon --repo ../my-service --telegram-chat <chat id> --telegram-user <your user id>
```

Only the listed users, in the listed chat, are heard. In the chat:
`/status`, `/pending`, `/milestone m1`, `/pause`, `/resume`, `/cancel m1`,
and `/budget m1 +20` (or `+2h`). A question left unanswered for a day
gets a reminder. After three reminders it takes its safe default, if
triage marked one, or is flagged in `status`.

### In the studio

The studio's **Board** tab is the owner's console:
- Run and Stop for the daemon.
- The checklist, where you tick rows and Build.
- Each milestone's columns, with a live line on every running card.
- The questions, the card graph, and each card's attempts as run pages.

The studio's assistant does the same from its chat, with its board tools.
The studio acts through `operonx project api <json>`, so it needs
`operonx-project` installed where it runs (or `OPERONX_PROJECT_BIN`).

### Bounds and boundaries

`--max-usd` and `--max-hours` bound a milestone (waiting for you does not
count toward the hours); a long rate limit pauses it until the reset, and
it goes on by itself. Coder sessions and checks never inherit a
credential from the engine's environment. A greenfield project gets its
own environment, made with its toolchain after `operonx init`. Checks run
on the host by default, or with no network in docker
(`check_mode="docker"`). See `operonx_project/guide/01-project.md`.
