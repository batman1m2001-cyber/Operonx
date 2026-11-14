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
3. BRD point 4, "Project-aware context compaction & on-demand knowledge retrieval" (added the same day):
   compaction writes the project's state — cards, requirements, decisions, knowledge — not a summary of
   the chat; a new session rebuilds its context from that state; docs are searched, not injected.

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
| Compaction into project state | the assistant's `compact` turn is Claude's `/compact`: a summary of the chat, kept in the session; on an account switch `_summarize` seeds a new session with a chat summary | nothing goes to the board, BRD or knowledge; a resumed session carries its whole history (median 25k, max 252k) |
| On-demand retrieval | `board_status`, `board_card`, `board_plan` (MCP) | every turn carries `knowledge.md` (14.6 KB), the briefing (every graph's first 40 ops) and AGENTS.md (4.5 KB) — ~6k tokens, cached, the same whatever is asked; no search over the BRD, plans, knowledge or guide |

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

- **Auto-compact** moved to E6: it saves into the project, not a chat summary.
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

### E6 — The project is the memory (BRD 4, studio + engine)

The conversation is working memory; the project files are the memory. Example: a 180k conversation
where the owner decided "retry the TTS twice, not three times" and C31 is half done. Today `/compact`
keeps a chat summary and the session goes on near 40k+. After E6 the save turn writes the decision to
`knowledge.md`, C31's outcome so far and next step to the board, and the next turn starts a **new**
session at ~8k: the rules, a pointer list, and a handoff of a few lines ("doing C31; next: the retry test").

- **Save, then start fresh.** When a turn ends above 100k context (a constant), on `/compact`, or on
  "New conversation" with unsaved work, the next turn is a *save* turn on the old session: it maps what
  the conversation produced to where it lives — a card's status/outcome (`board_card`), a new
  requirement (`docs/brd/requirements/`), a decision, pattern or pitfall (`knowledge.md`, kind
  `decision` beside E5's `pattern|pitfall|replaced`; no new file), open work (a card in `todo` or
  `needs you` with the next step). It ends with a handoff ≤ 1 KB (active card, files touched, the open
  question), stored with the chat. The next turn runs without `--resume`, seeded by the handoff only.
  The chat shows one "saved to the project" item listing what was written, each a link.
- **Goes:** Claude's `/compact` for the studio's turns and `_summarize` (the account-switch reseed uses
  the same save + handoff).
- **Pointers, not content.** `knowledge.md` keeps what applies every turn (tone, links, safety; target
  ≤ 4 KB); the rest moves to guide pages read when needed. The briefing names graphs, not their ops (the
  IR dump path stays). New MCP tools `project_search(query)` — one ranked search over the board, BRD,
  plans, `knowledge.md` and `.operonx/guide/`, answering heading + 3 lines + `path:line` — and
  `project_read(path, heading)` for one section. The same search is `operonx-project search <query>`, so
  an assistant outside the studio has it too; AGENTS.md says search before reading a whole doc.
- **A new session starts from state**: Now's "New conversation" seeds with `board_status`'s one line,
  the `doing`/`needs you` cards and the last handoff — nothing from the old transcript.
- **Gate.** (1) Continuity: on runline-lab, a scripted 3-step conversation (decide, half-build a card,
  save), then a fresh session asked "where are we, what's next?" answers from the board and handoff
  (Sonnet, 2 runs). (2) Fixed prompt per turn measured before/after (target ≤ 3k tokens from ~6k).
  (3) The studio's context meter on the next 20 real turns: max context and $ per conversation against
  the 69 measured in §1.

### E7 — Does OperonX save tokens? (BRD 3, measurement)

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
- E6's save + search is in arm A (both arms use the same assistant, so arm B gets plain Claude Code).
- **Report** in §4: per task and total, both arms, and the verdict — saves, breaks even, or costs more,
  and where (docs read vs. code navigated).

### E8 — Lint after every step (owner, 2026-11-15, studio)

The owner asked for a fast, rule-based check, run by default when a step ends, that the code still keeps
the conventions and the project's structure. `operonx lint` is that check (C3–C18, `ast` and regex only):
1.1 s on runline-lab, 3.5 s on the callbot (168 files), uv start included. Nothing runs it today: the
studio's Stop hook checks cards, failing tests, open steps and the board, never lint.

- The Stop hook: when the turn edited a file, run `operonx lint` on the project (15 s timeout). An error
  over the baseline sends the turn back once with the findings; warnings, held rules and a turn that
  edited nothing pass. A project without operonx installed, or a timeout, ends the turn untouched.
- **Gate:** a hook test per case (new error → back once with the finding; held by the baseline → ends;
  no edit → no lint run), and one real turn on runline-lab that adds a `print()` is sent back with C8.

## 3. Order and gates

E1 → E2 (one UI branch, screenshots) → E3 → E4 → E5 → E6 (needs E5's knowledge.md) → E7 (it measures
E4–E6).

- Studio: `node --test tests/js/` and `pytest` green; screenshots dark + light, desktop + phone; every
  old address still lands.
- Engine: `pytest` incl. `tests/guide`; the 18 init shapes green; lint on the projects at 0 stays at 0.
- E6: the three gates above. E7: the report table filled from real runs, under the cap.

## 4. Results

- **E1 + E2** (studio #184, bff376a): rail Project · Flow · Runs · Monitor · Settings; `#prail` and
  the back button gone; Environments and the project page under Settings; old addresses land. 69 lines
  in, 92 out; 194 JS tests; desk + phone, dark + light.
- **E3** (studio #185, 005223d): models Fable 5.2 / Opus 5.5 / Sonnet 5.5 / Haiku 5.5, 1M windows per
  the API reference; `knowledge.md` tone block (result first, no recap, no narrated next steps, no headers
  for a short answer), one line shorter, 14.6 KB; on glass the composer no longer sits over the words (the
  log fades under it with a mask — no fill can hide text on a see-through surface); inline code chip on
  `--border` (it was invisible on light glass). No new tokens; 194 JS tests, 58 assistant tests; desk +
  phone, dark + light.
- **E4**: `--web` writes `index.html` + `pages/home.js` + `components/ask.js` + `lib/api.js`; lint C17
  (`fetch(` outside `web/lib/`); AGENTS.md, guide 05 and the builder block say the layers and the card
  lifecycle. The persistence test found a real fault: `board.move` re-rendered the whole file, so one move
  on this board was an 11-line diff (cards re-sorted, blank lines added); it now rewrites that card only.
  Checks: the 18 init shapes (228 tests, lint clean incl. C17), the generated page in a browser ("Hello,
  Grace!", no console error). Adds more than it removes (+1 rule, +2 template files): BRD 1 asked for the
  layers. mr-finance has 4 `fetch(` outside `web/lib/`; its next lint needs `--baseline` (C17 = 4).
- **E5**: `init` writes `docs/brd/knowledge.md` (format + one example in a fence); `operonx guide` writes
  `.operonx/catalog.md` from source (`ast`, so a project that does not import still gets one; written only
  when it changes; git-ignored): the callbot's is 11 KB. A move to `done` asks for a pitfall or a pattern;
  the builder block says search first, edit never append; lint C18 warns on a stale `where` (a warning,
  so no baseline). Checks: 18 init shapes, lint + `brd check` clean on a fresh project, the done hint
  printed by the real CLI. Adds a file, a generated file and a rule; removes nothing.
- **E6, engine**: `operonx-project search QUERY` ranks the sections (heading to heading, never inside a
  code fence) of `docs/brd/`, `docs/*.md`, `.operonx/guide/`, the catalog and AGENTS.md: BM25, a heading
  word counted 3×, plurals and `-ing` folded; it answers heading, the 3 lines that hold the query and
  `path:line`. `operonx-project read PATH [HEADING]` prints one section with its sub-sections (a heading
  matches exactly or by its start: `read docs/brd/board.md C29`). 0.47 s on this repo. knowledge.md has
  the kind `decision` (what the owner chose and why); the builder block says search before reading a
  whole doc. Checks: 4 search tests, 58 guide + lint, the 18 init shapes (228).
