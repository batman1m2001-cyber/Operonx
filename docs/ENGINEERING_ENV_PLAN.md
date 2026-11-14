---
title: One engineering environment — the menu, the workflow, the knowledge, the token count
---

# One engineering environment

**Asked** (owner, 2026-11-14). Three asks merged into one plan:

1. The studio: a 5-item menu, Project mode folded into it, and assistant fixes (shorter replies, a cheaper
   long conversation, the current models, a font/colour pass).
2. The BRD "OperonX Refactoring — Development Workflow & Knowledge Management": (1) a standard workflow
   that `operonx init` gives every project, whatever the assistant; (2) reusable knowledge — patterns, ops,
   pitfalls — kept by the workflow, not by memory; (3) an objective token comparison: OperonX vs. from
   scratch, same assistant.

LEAN_PROJECT_PLAN's rule holds: **each phase removes more than it adds**, or the PR says why not.

---

## 1. Audit — what exists today (2026-11-14)

| BRD ask | Already there | Gap |
|---|---|---|
| Core architecture & conventions | `operonx/guide/` (10 pages, every snippet tested), copied into `.operonx/guide/` by `operonx init` / `operonx guide`; `AGENTS.md` (the ladder, layout, rules, commands); `operonx lint` C13–C16 checks the shape | — |
| Web UI structure | one line: "pages are `web/`, they call the server only through `web/lib/api.js`" | no layers, no components rule |
| Project board, persisted locally | `docs/brd/board.md`, 4 fields / 4 states, cycles; `test_parse_and_render_round_trip`; the studio's Board reads it | no test that a studio move leaves the rest of the file byte-identical |
| Task lifecycle | AGENTS.md builder block: `operonx-project board C4 doing`, done when its `done` tests pass (the studio runs them) | when to move to doing / needs you is not said |
| Any assistant | `AGENTS.md` is the cross-tool file (Codex, Cursor, Copilot, Gemini via config read it); `CLAUDE.md` is `@AGENTS.md` | — |
| Reusable knowledge, pitfalls | nothing in projects: LESSONS.md / practices.toml went in LEAN L1 | all of BRD 2 |
| Token comparison | nothing | all of BRD 3 |

The studio (operonx-studio main 681a3f2): the rail has 7 hubs (Project, Flow, Runs, Monitor, Improve,
Operate, Settings); Project mode swaps the rail for `#prail` (Now, Board, Git, Files, Terminal,
Environments, Settings) behind a "Studio" back button — the owner could not find the way back. The
assistant: 69 turns measured, median reply 262 chars, but the cost is context: median 25k, max 252k, the
top 5 turns 83% of $34 ("hello ?" at 215k context cost $6.67). Replies are not the problem; long contexts
are. The model list names fable 5-1, sonnet 5, haiku 4-5.

## 2. Phases

### E1 — Five items in the menu (studio)

```
Project   Now · Board · Git · Files · Terminal
Flow      Flow · Playground · Prompts · Services · Jobs (· Knowledge)
Runs      Runs · Review · Evals
Monitor   Monitor · Alerts
Settings  Resources · Environments · Storage · Project
```

Improve and Operate go (Prompts are made in the flow, Evals judge runs; Services and Jobs are how a flow
runs). The `#hubtabs` strip and `hubLast` stay as built in #183.

### E2 — Project mode is the Project hub (studio)

No mode, no `#prail`, no back button. Project is a hub like the others; its pages are panes. Now keeps the
conversation in the centre; on the other pages it stays on the right as today. Environments and the
project's Settings page (standard + git policy) move to the Settings hub. `/p/<pid>/project/<page>` keeps
working (router maps it to the pane). Goes: `#prail`, `body.pmode` (18 CSS rules), the mode switch and
its state, `ProjectMode.go` callers rerouted. Same screenshots as #183 (desktop + phone, dark + light).

### E3 — The assistant (studio)

- **Auto-compact.** When a turn ends above 100k context, the next turn is preceded by the existing
  `compact` turn (no new mechanism). Threshold a constant; shown in the chat as the compact item it is.
- **Tone.** `knowledge.md`: result first, no recap of what was done, no narration of next steps, no
  headers for a short answer. Net lines down.
- **Models.** Fable 5.2 `claude-fable-5-2`, Opus 5.5 `claude-opus-5-5`, Sonnet 5.5 `claude-sonnet-5-5`,
  Haiku 5.5 `claude-haiku-5-5`; windows checked against the API reference, not guessed.
- **Font and colour.** Screenshot one real conversation (text, code, a table, a tool row) dark and light;
  fix what does not match the theme tokens. No new tokens.

### E4 — The workflow every project starts with (BRD 1, engine)

- **Web layers** in AGENTS.md's `--web` block and guide page 05: `web/index.html` + `web/pages/<page>.js`
  (one per screen), `web/components/` (pure render functions, no fetch), `web/lib/api.js` (the only
  `fetch`), `web/assets/`. The `--web` template follows it (one page, one component).
  `operonx lint` gets one rule: `fetch(` outside `web/lib/`.
- **Task lifecycle**, in the builder block: move a card to `doing` before the first edit, to `needs you`
  with the question when blocked, to `done` only through its tests; one card in `doing` at a time.
- **Persistence check**: a test that a studio card move rewrites only that card's heading line.

### E5 — Knowledge that the workflow keeps (BRD 2, engine)

- **`docs/brd/knowledge.md`**, written by `operonx init`: one entry per reusable thing,
  `### K3 · title · pattern|pitfall|replaced`, then `where` (files), `use` (when to reach for it),
  `instead` (what replaced it, for `replaced`). Pitfalls carry the symptom, so a search finds them.
- **The op catalogue is generated, not written**: `operonx guide` also writes `.operonx/catalog.md` —
  every op, graph and job the app declares, with its signature and the first line of its docstring.
  An assistant reads it before writing a new op. Nobody maintains it.
- **The workflow asks, not memory**: moving a card to `done` prints "anything reusable or a pitfall?
  docs/brd/knowledge.md"; the builder block says the same and says to edit, not append, when a pattern
  is replaced. `operonx lint` warns on an entry whose `where` file is gone (stale knowledge).

### E6 — Does OperonX save tokens? (BRD 3, measurement)

Same assistant both arms: Claude Code headless, **Sonnet 5.5**, one fresh session per task (as real tasks
are), the same task prompts word for word.

- **Arm A:** `operonx init support --web --http` and its guide. **Arm B:** an empty folder, the same
  spec, any library.
- **Product:** a support-ticket triager — an HTTP route that classifies a ticket with an LLM, a nightly
  job that summarises the day, a web page listing tickets. Model calls in the product go to a fake model.
- **Tasks** (one per BRD bullet): T1 set up + first feature (architecture understanding); T2 a second
  feature; T3 a third feature of T2's shape (repeated work); T4 change T2's behaviour; T5a/T5b the same
  planted bug class twice (recurring debugging); T6 a new page.
- **Equal output first**: hidden acceptance tests per task; one fix turn allowed, and counted. A task
  that fails in one arm is reported, not compared.
- **Counted** from stream-json `usage`: input, cache-creation, cache-read, output, turns, cost; plus the
  tokens read from docs (Read results on `.operonx/guide/`, AGENTS.md, knowledge.md, chars/4).
- **Runs:** 2 repetitions per arm → 28 sessions. **Budget cap $40 notional** (`total_cost_usd`, the
  subscription's figure); the runner stops at the cap. Harness in `bench/token_ab/`, outside the package.
- **Report** in §4: per task and total, both arms, and the verdict — saves, breaks even, or costs more,
  and where (docs read vs. code navigated).

## 3. Order and gates

E1 → E2 (one UI branch, screenshots) → E3 → E4 → E5 → E6 (it measures E4 + E5).

- Studio: `node --test tests/js/` and `pytest` green; screenshots dark + light, desktop + phone; every
  old address still lands.
- Engine: `pytest` incl. `tests/guide`; the 18 init shapes green; lint on the projects at 0 stays at 0.
- E6: the report table filled from real runs, under the cap.

## 4. Results

(filled per phase)
