---
title: Project mode — the studio as the place a project is built from
status: in_progress
phases:
  - {id: P0, title: The studio reaches the engine, status: open}
  - {id: P1, title: Plans carry their status, status: open}
  - {id: P2, title: One status for the whole project, status: open}
  - {id: P3, title: Project mode, chat first, status: open}
  - {id: P4, title: The pages behind the icons, status: open}
  - {id: P5, title: The assistant works by the conventions, status: open}
  - {id: P6, title: Gate on OneWealth and tipcalc, status: open}
updated: 2026-10-23
---

# Project mode: the studio as the place a project is built from

**Status:** 2026-10-23, approved by the owner ("write the plan, and go"). Every claim about today was read in
the code (Operon `65a9e562`, operonx-studio `1d67366`) or measured on OneWealth (`mr-finance` `8880681`,
read-only).

## 1. What the owner wants

The owner builds projects by talking to Claude Code. They want the studio to be that place: a chat with an
assistant that does the work through Claude Code, following the project's conventions (the BRD checklist,
plans, practices, checks), while the screen shows plainly what is happening, what needs them and what is
next. Not a dashboard with a chat beside it: a chat with the project's state beside it.

## 2. What is there today, and what is wrong

- **The assistant already is Claude Code.** `chat.py` runs `claude` in the project root with edits and Bash
  (`--permission-mode acceptEdits`), the studio's MCP tools and `knowledge.md` as its brief. Nothing to
  build there; what it lacks is the conventions for work done directly in chat, and a way to say where the
  project stands.
- **The live studio cannot reach the engine.** `operonx-project` is not installed in the studio's venv
  (`.venv/bin/operonx-project` does not exist; `install.sh` never installs it). Every Board action (Run,
  New request, an answer) goes through `operonx-project api` and so has nowhere to go.
- **The Board shows only what the engine built.** OneWealth has 224 commits, 137 since 10-16, all made in
  Claude Code sessions, and no engine state: the Board says "No milestone yet". Its checklist is right
  (322 rows, 305 verified; all 419 cited tests exist; the latest commit updated it) because AGENTS.md tells
  every session to keep its row true. Its 21 plan docs are not: 14 have no status line and 2 say the wrong
  thing (`ASSISTANT_PLAN` "in progress", `NEWS_IMPACT_PLAN` "plan"; both are done). What is in flight lives
  in prose and in an agent's memory, and the studio shows none of it.
- **The Board is hard to follow.** One page holds a milestone picker, four view tabs, a row of chips
  ("unsandboxed", "models: sonnet", "TDD strict · layered, 4 layers…", "daemon running"), a retrospective as
  a wall of text and a six-column kanban that is mostly "—" for six cards. Its words are the engine's: W1,
  held-out, red-first, arbitration, baseline. "What is happening" and "how is it set up" share one screen.

## 3. Decisions

### D1. A plan doc carries its status as front matter

Any Markdown file under `docs/` that starts with this block is a plan (YAML front matter: GitHub renders it
as a table):

```yaml
---
title: Wealth names
status: in_progress        # plan | in_progress | done | parked
phases:
  - {id: W0, title: Names in the glossary, status: done}      # open | in_progress | done | skipped
  - {id: W1, title: The four groups on Toàn cảnh, status: in_progress}
rows: [OWN-WN-01, OWN-WN-02]   # the checklist rows it serves (optional)
updated: 2026-10-22
---
```

The engine reads it (`operonx_project.plans`); nothing else is needed. `operonx-project brd check` fails on
a block that does not parse, an unknown status, a row the checklist does not define, or `status: done` with
a phase still open; a `*_PLAN.md` without a block is a warning, so a project adopts it at its own pace.
AGENTS.md's builder block (written by `operonx init`, kept current by `operonx guide`) says: a plan starts
with this block, and a phase's status changes in the commit that finishes it. That is how the checklist
already stays true, and it is the only way status stays true when work happens outside the studio.

Rejected: plans as engine milestones (`operonx-project start` for every piece of work). A milestone runs
coders, reviews and held-out tests; most work done in chat needs none of that, and the owner would stop
recording it.

### D2. One status, computed by the engine, read by the screen and the assistant alike

`operonx-project api {"op": "status"}` (and `operonx-project status`) returns one document:

- `checklist`: counts by status, the open rows (not verified, not out of scope);
- `plans`: every plan doc and every engine milestone, one shape: title, status, phases with their status,
  rows, where it came from, last change; ordered in progress, then planned, then done (recent first);
- `needs_you`: the engine's open questions and approvals, and the BRD's open questions;
- `working_on`: what is in progress, each with one sentence written by code from the data ("W1 of 3: The
  four groups on Toàn cảnh"; "C5 running, round 7, $2.76 of $5");
- `changes`: the last commits (and the engine's last events), newest first;
- `setup`: practices in one line, the models, the daemon, the budget.

No model writes any of it. The screen and the assistant read the same document, so they never disagree.

### D3. Project mode: a mode of the studio, chat first

The sidebar's Board item becomes **Project**. It switches the studio into Project mode at
`/p/<pid>/project[/<page>]`: the main menu is replaced by the project's own icon bar, and the main area is
the conversation with the assistant, with the project's state in a column beside it:

```
┌──┬─────────────────────────────────┬──────────────────────┐
│◉ │ You: add CSV export to the      │ NEEDS YOU (1)        │
│▤ │      wealth report              │  Q-10 · C6 …         │
│☑ │                                 │ WORKING ON           │
│? │ Assistant: two new rows, and a  │  CSV export · 1 of 3 │
│↻ │ plan of three steps: …          │  Wealth names · W1   │
│⚙ │                                 │ CHECKLIST 305 / 322  │
│← │ ▸ editing 3 files…              │ RECENT · 5 commits   │
└──┴─────────────────────────────────┴──────────────────────┘
```

| Icon | Page | Answers |
|---|---|---|
| ◉ | Now (home) | The conversation, and beside it: what needs me, what is running, what just happened |
| ▤ | Plans | Every plan and milestone as phases on one line each; a milestone opens its cards (the kanban, now one view of one milestone) |
| ☑ | Checklist | The requirements: done, open, and their tests |
| ? | Questions | The decisions waiting for the owner, answered in place; the count is on the icon |
| ↻ | Changes | Commits, rounds, ships, lessons, newest first |
| ⚙ | Settings | Practices, models, budget, the daemon: off the main screen |
| ← | | Back to the studio |

Words are plain: "hidden tests" (held-out), "plan first, then build" (W1), "tests written first" (strict
TDD). Details open on a click. The status column refreshes after every assistant turn, every 15 s while
visible, and when the project's files change (the studio already watches them).

### D4. The studio's own menu is an icon bar by default

The rail already slims to icons below 1200 px (`shell.js`) and remembers a choice. It becomes slim by default
at every width (a choice still wins), with the group names as thin dividers and each icon's name on hover.
Project mode's bar is the same component with its own icons, so both feel the same.

### D5. The assistant works by the conventions, and says where things stand from D2

`knowledge.md` gains the way to work directly: a small change is made and its row kept true; anything with
more than one step gets a plan doc with the D1 block, worked phase by phase, the block updated in the
commit that finishes a phase; the engine (`board_request`) only when the owner wants it built with
reviews and hidden tests. A new MCP tool, `project_status`, returns D2's document as text: "where are we",
"what's next", "what needs me" are answered from it, never from memory.

### Rejected outright

- **A separate app for the builder.** One studio, one sign-in, one assistant; a mode, not a product.
- **A model summarising the status.** The status is data; the assistant narrates it when asked.
- **Clean Architecture rings by default.** The practices preview on five repos found the operonx layering
  catches what matters; rings stay a per-project choice (`clean` preset) where a real domain exists.

## 4. Phases

One branch per phase per repo, merged when its gate passes. Tests use fixtures and scripted coders; model
calls only in P6, on Sonnet.

| Phase | What | Gate |
|---|---|---|
| P0 | The studio's `install.sh` installs operonx-project beside operonx; the live studio gets it; Project mode (and today's Board) says plainly when the engine is missing instead of failing an action | `operonx-project api` answers from the live studio's environment; a studio test with no engine shows the message |
| P1 | `operonx_project/plans.py` (read, validate), `brd check` rules, `operonx-project plans [--check]`, the AGENTS.md builder block line (core), guide pages | Unit tests per rule; a project without plan blocks checks as before |
| P2 | The `status` op and `operonx-project status`; the sentences | Fixture tests; on a copy of OneWealth it returns the 7 open rows, the 6 open questions and every plan, in under 1 s |
| P3 | Project mode: the route, the icon bar, the chat-first layout (the assistant's placement in the centre), the status column, the main rail slim by default | Studio tests; screenshots desktop and phone with no sideways scroll |
| P4 | Plans, Checklist, Questions, Changes, Settings pages; `/board` opens Project mode | Studio tests; screenshots of each page |
| P5 | `project_status` MCP tool; `knowledge.md`: direct work by the conventions, status from the tool | Studio tests (the tool's text from a fixture) |
| P6 | On a copy of OneWealth, through Project mode's chat (Sonnet): "where are we?"; "add the status block to every plan" (21 plans, `brd check` clean, the two wrong ones corrected); on tipcalc, a fix asked in chat (its checklist-count test) done by the conventions, the status column following it live | The answers match the data; desktop and phone screenshots; every suite green; the live studio restarted on it |
