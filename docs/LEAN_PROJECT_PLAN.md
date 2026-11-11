---
title: One lean plan — the project's shape and its board
status: done
phases:
  - {id: L1, title: Cut first, status: done}
  - {id: L2, title: "operonx init: three templates, three flags", status: done}
  - {id: L3, title: Four lint checks and a baseline, status: done}
  - {id: L4, title: The board, status: done}
  - {id: L5, title: mr-finance on the board and the ratchet, status: done}
updated: 2026-11-11
---

# One lean plan: the project's shape and its board

**Asked** (owner, 2026-11-11). "What is left in the plan? I fear overengineering most: ending up with a rough heap,
hard to control, like the code base today."

This plan replaces [PROJECT_CONVENTION.md](PROJECT_CONVENTION.md) and
[PROJECT_MODE_V2_PLAN.md](PROJECT_MODE_V2_PLAN.md). Their pictures stay as the reference; their scope is cut
to what is below.

---

## 0. The one rule

**Every phase removes more than it adds.** Each phase PR states, before and after: lines in the engine and the
studio, files `init` writes, lint checks, config keys. A phase that grows them does not merge.

## 1. The budget

| | the two plans | this plan |
|---|---|---|
| new lint checks | 10 (C13–C22) | **4** (C13–C16) |
| `operonx init` | 7 parts, 72 shapes | **3 templates × 3 flags, 18 shapes** |
| unit templates, `operonx add` | 10 | **0** now |
| card fields | 6 (lane, unit, req, after, files, done) | **4** (req, after, files, done) |
| card states | 5 | **4** (todo, doing, needs you, done) |
| plans | 2 | **1** |
| `practices.toml` | slimmed | **cut** |

What there is to cut today (lines):

```mermaid
xychart-beta horizontal
  title "Engine flows and studio project page (lines)"
  x-axis [task, lifecycle, prompts, brd, final, discover, retro, plan, "projectmode.js", "board.js", "hand.js"]
  y-axis "lines" 0 --> 1100
  bar [920, 848, 560, 403, 252, 220, 191, 142, 1045, 928, 226]
```

The engine is 14,553 lines, `operonx lint` 914, `init.py` 359, the studio project page 2,406.

---

## 2. The shape

One picture per question. These are **doc rules**: nothing checks them until a count shows they drift.

| | picture |
|---|---|
| a feature's files | [layout.svg](project-convention/layout.svg) |
| the whole repo | [tree.svg](project-convention/tree.svg) |
| the web part | [web.svg](project-convention/web.svg) |
| who imports whom | [imports.svg](project-convention/imports.svg) |

![One project, one shape](project-convention/layout.svg)

- Fixed file names in a feature: `__init__` `routes` `jobs` `graph` `ops` `rules` `store` `source` `prompts`
  `agent`; anything else is `_private.py`.
- Code every feature needs goes in `src/_shared/`, which imports no feature.
- Backend is `app/` + `src/`; frontend is `web/`; runs write only to `data/` (keep) or `out/` (throw away).

Doc only, not checked: file names, tests mirroring `src/`, 500 lines per file, `fetch` only in `lib/api.js`,
where binaries go.

---

## 3. Four checks, on a ratchet

| | rule | mr-finance | callbot |
|---|---|---|---|
| **C13** | another feature only through its `__init__` names or its graph | 401 reach inside | 15 |
| **C14** | every endpoint is a `Service` | 63 hand routes | 0 |
| **C15** | env is read only in `src/_shared/config.py` | 51 reads, 20 files | 30 reads, 10 files |
| **C16** | the root holds only the allow-list; generated files only in `data/` or `out/` | 17 loose | 17 loose |

Each one is a hop in a bug hunt ([debug.svg](project-convention/debug.svg)): C14 gives every request a trace,
C13 and C15 keep the line in the folder the URL names.

```mermaid
flowchart LR
  B["operonx lint --baseline<br/><small>today's count per check</small>"] --> L{"operonx lint"}
  L -- "a count went up" --> R(["red"])
  L -- "same or lower" --> G(["green · the baseline drops"])
  C["a card touches a file"] --> M["move it into the shape"] --> L
```

New files are always clean. Old files move only when a card touches them.

**L3, done.** `operonx lint` checks C13–C16 in any folder with an `operonx.toml` (`src/` for C13; tests and
`scripts/`/`tools/` are not checked). `--baseline` writes today's count per rule into `[lint] baseline`, lint
is red only above it and says when it can drop, `--all` ignores it. Eval records moved from a root `evals/` to
`.operonx/evals/` (the old folder is still read), so the engine no longer breaks C16 itself. Numbers: four checks,
in budget; `operonx lint` 1,046 → 1,303 lines; per project, errors C13 / C14 / C15 / C16 before → after:

| project | before | after |
|---|---|---|
| tipcalc | 0 / 0 / 0 / 1 | **0** |
| meeting-prep-operonx | 0 / 0 / 1 / 1 | **0** |
| meeting-prep-brd | 13 / 0 / 1 / 2 | **0** |
| qc-snatcher | 19 / 0 / 101 / 8 | `src/` **0**; 97 env reads in `deployment/` and the root held |
| callbot | 8 / 4 / 29 / 17 | held (L5) |

qc's root (`main.py`, `models.yaml`, `knowledge/`, `Dockerfile`, `deployment/`) is the bank's deploy contract:
the Dockerfile copies those paths and `deployment/` is their ingestion code, so it stays and the baseline holds
it, against the gate's 0. Its `src/core` became `src/_shared`.

---

## 4. `operonx init`

![One template, up to three flags](lean-plan/init.svg)

```toml
# operonx.toml — the only new key
[project]
parts = ["http", "web"]
```

- `operonx init . --web` later writes only missing files. For a file you edited (`app/main.py`) it prints the
  line to add.
- **Behaviour change:** plain `operonx init` gives offline `hello`. A doc that says "init, then serve" now says
  `operonx init --http`.

**L2, done.** Templates `hello` · `chat` · `agent` × `--http` · `--web` · `--deploy`; the `http` template is gone
(`hello --http` is it). All 18 shapes pass their own tests, ruff and `operonx lint`; `init . --web` on a chat
project gives the same files as `init -t chat --web`, and an edited `app/main.py` is kept with its missing lines
printed. Numbers: templates 47 files / 927 lines → 52 / 930; generated, comparable shape (with a service):
hello 15 files / 392 lines → 20 / 343, chat 16 / 402 → 22 / 383, agent 14 / 405 → 21 / 398; plain `init`
(hello, offline) 18 / 293. `init.py` 353 → 487 and `main.py` 244 → 254: the sections and the re-init are the
growth, and the one place this phase does not meet §0. Config keys +1 (`parts`), lint checks 0.

---

## 5. The board

![The board: lanes by colour, four columns](project-mode-v2/board.svg)

A card in `docs/brd/board.md`:

```markdown
### C4 · gold jump alert · doing
req: NEWS-07 · after: C2
files: src/news/rules.py, tests/news/test_rules.py
done: tests/news/test_rules.py::test_gold_jump
```

- **The lane is read from `files`** (`web/` → UI, `routes.py` → web, `ops`/`rules` → core …), not written.
- **Two kinds:** *build* (a requirement) and *fix* (a red test or a bug).
- **States:** `todo → doing → done`; `needs you` when the assistant asks, or a card adds a file outside the
  fixed names. A card whose `after` is not done stays in todo, marked.
- The running card shows the live progress line (kept from today's `progress.py`).

```mermaid
stateDiagram-v2
  direction LR
  state "needs you" as ask
  [*] --> todo
  todo --> doing: after done
  doing --> ask: a question, or a new file name
  ask --> doing: owner answers
  doing --> done: done tests green
```

**L4, done.** The board is one file, `docs/brd/board.md`, and `operonx_project.board` reads and writes it
(`put`, `move`, `check`): four fields (`req`, `after`, `files`, `done`), four states, the lane read from `files`
and the kind from `req`. `move` refuses doing while an `after` card is open, and done with no `done` test; the
studio runs the `done` tests before done and refuses on a red one. The milestone boards, `start`/`replan`/the
daemon and the studio's plan mirror went; a repo's old board keeps only its open cards, as todo, and cites the SHA
that holds the rest. The studio reads the board in-process; a turn that moves a card to doing is that card's
turn, and the card shows its step, or its last tool. Numbers: engine 5,306 → 4,229 lines (its tests 2,076 →
1,854); studio 55,269 → 54,843; `init` writes one file fewer; fields 6 → 4, states 5 → 4. Gate: Sonnet put four
tipcalc cards on the board and worked them one at a time, each done on a green test (studio #156, light and dark
screenshots). What it wrote by hand (`- req: none`) is now read as fields.

---

## 6. Cut first

| keep | cut |
|---|---|
| board store + reader | discover / plan / final flows |
| `brd check`, on `requirements.md` only | review → held-out → QA stages, the examiner |
| `status`, one read-only summary | `practices.toml`, retro, LESSONS |
| studio turn checks, stop hook, `failing_on_base` | plan status blocks → milestone mirror |
| live progress (`progress.py`, runhook) | pages: Checklist · Questions · Changes · Graph |
| `operonx init` | `hand.js` |

Before each cut: grep for a second job. The Telegram bridge went too (the owner's call), with the daemon.

**L1, done** (studio #155, this PR): the daemon, the coding stages, Telegram, practices, ship/needs/request
and take/submit are gone; `api` keeps `plan` and `status`. Engine src 14,553 → 5,306 lines, its guide
492 → 185, its tests 6,464 → 2,076; studio Project page 2,406 → 1,532, `mcp.py` 1,206 → 888, board tools
12 → 3. Two keeps differ from the table: `hand.js` has a second job (it is the studio assistant's hand on every
page, not only the board's) and stays; the plan mirror stays until L4 replaces it, as mr-finance reads it today.

## 7. Not now, and what brings it back

| not now | comes back when |
|---|---|
| `operonx add`, unit templates | the same boilerplate breaks two cards |
| more lint checks (names, tests, size, fetch, binaries) | a count shows the doc rule drifting in two projects |
| init `--jobs` `--store` `--llm` | two new projects hand-write the same part |
| lane as a card field; a `waiting` state | the owner cannot read the board without it |
| a web build step | a project needs React or Vite |

---

## 8. Phases

```mermaid
flowchart LR
  L1["<b>L1</b> cut<br/><small>§6</small>"] --> L2["<b>L2</b> init<br/><small>3 templates · 3 flags</small>"]
  L2 --> L3["<b>L3</b> lint<br/><small>C13–C16 · --baseline</small>"]
  L3 --> L4["<b>L4</b> board<br/><small>4 fields · 4 states</small>"]
  L4 --> L5["<b>L5</b> mr-finance<br/><small>one real task</small>"]
```

| | gate |
|---|---|
| L1 | engine and studio suites green; lines before and after, down |
| L2 | all 18 shapes pass `operonx lint` and their tests; `init . --web` on an existing project adds only missing files |
| L3 | tipcalc, qc, meeting-prep ×2 at 0; one deep import added on purpose turns lint red |
| L4 | light and dark screenshots; the assistant works one task card by card |
| L5 | the owner follows one real mr-finance task on the board alone; its first move (`control/` and the shared parts of people/wealth → `src/_shared`) takes 197 off C13; :8030 still serves; callbot green on a baseline |

**L5, done.** mr-finance #75 (045fe98): the board is `docs/brd/board.md`, and the 8 cards still open on the
milestone boards m1–m33 are its todo cards (34 files → 1, history in git at 56013cd). `wealth/{paths,money,text,labels}`
and `control/errors` moved to `src/_shared`; `control/roles` stays, because it imports people. C13 went 179 → 107: −72, not
the −197 the table asks for. That 197 was counted on the 401 reaches before mr-finance #74 cut them to 179. The 107
left are feature-to-feature reaches, held on the baseline (`C13 107, C14 63, C15 48, C16 3`). C12 went 8 → 0 (the
env names are now in `.env.example`). Tests: 1,256 passed; the 4 that failed also fail on main. :8030 serves 045fe98.
The callbot is green on its baseline (059962f), and its board is one empty `board.md` (24c209d). One gate item is the
owner's and still open: following one real mr-finance task on the board alone. Its 8 cards wait on the owner, so the
assistant did not start one; the L4 gate (studio #156, tipcalc C1–C4) showed a task worked on the board card by card.
