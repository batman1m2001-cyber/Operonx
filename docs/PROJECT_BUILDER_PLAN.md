# The project builder: the board lives in the BRD, any Claude Code works it

**Status:** 2026-10-19, approved by the owner ("update the plan, and go"); B10 added on their ask; the sandbox question answered (§10). Every "today" below was read in the code
(Operon `f78752ef`, operonx-studio `2b44cdd`). Nothing in it is built except where a phase says
"built, unmerged".

**Progress (2026-10-19):**
- B1–B6 merged (Operon #214–#215, studio #98–#99); the models setting, the stage budget and take-ahead merged
  (Operon #216–#218, studio #100–#102).
- The held-out tests read the project's pytest config (Operon #219). They ran outside the repo and lost a
  `src/` layout's `pythonpath`, so tipcalc's C2 failed 6 rounds with passing code.
- B10 (D14–D18) built on `feat/builder-b10` in both repos. B7 on tipcalc found two more defects, fixed
  there:
  - a taker's empty submit was verified, and a `docs` card with no change was too;
  - a final finding that named a card but cited none of its files failed the whole milestone and did
    not re-open the card.

The owner's model, which this plan follows:

- A project has two layers. **`docs/brd/`** says what the product must do and how far it is (the
  requirements and their checklist). **The board** is how it is built: every milestone, every card
  with its own plan (what it does, the rows it serves, its criteria, its dependencies, the files it
  touches) and its status.
- With both, a project can be rebuilt **in a similar way**: the same cards, in the same order,
  checked by the same named tests. The code differs; the result does not differ much.
- A new feature adds rows to the checklist (not built), a milestone turns them into cards (Backlog →
  In progress → Done), and the rows flip to verified as the cards pass.
- The work is done by the owner's Claude Code, from the studio's assistant or a terminal, and the
  Board moves while it works.
- Every project made with `operonx init` has all of this from the start (opt-out), and the usage
  docs say so in one place.
- **The studio is the owner's console.** The owner no longer drives the work from an editor chat:
  they say what they want in the studio's assistant, watch the Board, answer its questions and
  approve shipping there, from a desktop or a phone (B10).

---

## 1. What exists today

| Piece | Where | What it does today | Gap against the model |
|---|---|---|---|
| BRD folder | `operonx_project/brd/template/`, `brd/folder.py` `init_brd` | `operonx-project brd init` / `new` write it: sources, requirements, matrix, questions, features, ADRs, testing | No layer for the board; nothing says which card built a row |
| Board | `.operonx/project/project.db` (`store.py` tables `milestones`, `cards`, `events`, `steps`, `interactions`, `commands`, `lease`) | The whole plan and status, in SQLite | **Excluded from git** (`project.py` `_exclude`): a clone loses the plan; a deleted file loses the project's history |
| Checklist status | `flows/final_flow/ops.py` `record_rows` → `set_matrix_row` | A row becomes `implemented_verified` at the end of a milestone, when its tests pass | Fine; kept |
| Who works a card | `coder/claude.py` `ClaudeCodeCoder` (`claude -p`), run by `operonx-project daemon` | The daemon plans, implements, reviews and QA-checks every card | Only the daemon can work a card; the owner's own Claude Code (assistant, terminal) cannot take one |
| Sandbox | `coder/roles.py` `sandbox_settings` (bubblewrap) | On by default | This host refuses it: `kernel.apparmor_restrict_unprivileged_userns = 1` (read 2026-10-19), so every real run needs `--no-sandbox` (P7's wall) |
| Studio Board | `operonx_studio/projectboard.py`, `static/board.js` (studio #96) | Board / Checklist / Graph, read-only from `project.db`; actions through `operonx-project api`; refresh by event cursor every 3 s | Tab only when `project.db` exists; no live "what the coder is doing" |
| Board before a milestone | studio `feat/board-project-checklist` (**built, unmerged**, `2c3f72c`) | The tab shows `docs/brd`'s checklist from the checkout when there is no store | Becomes part of B5 |
| Studio assistant | `operonx_studio/assistant.py`, `chat.py`, `mcp.py` | Headless Claude Code in the project's folder, with studio MCP tools (runs, monitor, evals, jobs, play, new project) | No board tools: it cannot see or drive the builder |
| `operonx init` | `operonx/cli/init.py`; Operon `feat/init-brd` (**built, unmerged**, `eadac858`) | The branch writes `docs/brd/` by default (`--no-brd`), the template moved into operonx (`operonx/cli/templates/_brd/`, `brd_files`) | No board folder; the AGENTS.md section is static, so older projects never get it |
| A project in a subfolder | `gitops.py` `GitOps.__init__` (`rev-parse --show-toplevel`) | State and BRD go to the **git top level** | `meeting-prep-operonx` and `meeting-prep-brd` share one repo: they would share one store, one BRD and the branch `milestone/m1/main` |
| Usage docs | 12 files (§9) | Each says part of it | Nothing describes the two layers, the board files, or how an assistant works a card |

---

## 2. Decisions

Made here, each with the reason. The owner answered the one open question (§10).

**D1. The board moves into the BRD, as text: `docs/brd/board/`.** One file per milestone,
`docs/brd/board/m1.md`, and an index, `docs/brd/board/README.md`. `.operonx/project/` keeps only
what a machine needs while it runs: the event stream, leases, the command queue, journals, coder
transcripts and budget counters. *Why:* the plan is the project's, so it is in git, travels with a
clone, and its changes show in a PR diff. The runtime state changes every second, is large, holds
transcripts that may carry customer data, and would conflict on every branch.

**D2. The file format is Markdown the engine writes and reads back.** A card table (ID, title,
rows, deps, kind, status), then one `### C3 · title` section per card (intent, criteria, files,
outcome: the tests that passed, the run, the cost). *Why:* the BRD is Markdown, reviewers read it
in a PR, and the studio already parses its tables. A second format (JSON) would be a second thing
to keep true. A round-trip test (store → files → empty store → same board) is the contract.

**D3. One status per thing.** A card's status lives on the board; a requirement's status lives in
the matrix. They cite each other (a card lists its rows; a row's evidence names its card, `m1/C3`).
Neither copies the other's status, so they cannot disagree. `operonx-project brd check` enforces
the links: every card's rows exist, every `planned` row names its card or milestone.

**D4. The files are written at the moments that matter, never per event:** plan approved, a card
done / stopped / failed / cancelled, a milestone ended. They are committed on the milestone's branch
(`milestone/<m>/main`), in the card's merge commit when it can be, so the board on that branch is
always the board of that branch's code. *Live* status (the 3-second view) comes from the store, not
the files.

**D5. The store can be rebuilt from the files.** When `.operonx/project/project.db` is missing (a
fresh clone, a deleted file), `Project.open` rebuilds the board from `docs/brd/board/`. A card that
was mid-flight comes back `ready` (its worktree is not in git), with its run id kept as a link.

**D6. Reproduce = `operonx-project start --from-board <milestone>`.** It creates a new milestone
whose plan is that board's cards, all `pending`, and skips planning. The owner still approves the
plan (one tap). The owner's earlier answers reach the cards through `open_questions.md`, where every
answered `Q-nn` keeps its resolution. *Why:* this is the owner's "rebuild it in a similar way",
with no replay of transcripts (they are the parts that should differ).

**D7. Two ways to work a card, one set of gates.**
- *The daemon* (unattended, parallel): as today; its coders are Claude Code on the owner's sign-in.
- *Take and submit* (one card, by hand or by an assistant): `operonx-project take m1 C3` moves C3 to
  In progress with the taker as actor and prints its worktree, its criteria and its rows;
  `operonx-project submit m1 C3` runs the same review and QA, held-out tests included, as a daemon
  card. A failing submit goes back to In progress with the findings; a passing one is merged and Done.

  Any Claude Code can do it: the studio assistant through its tools (D9), or the owner's terminal
  session through the CLI. *Why:* the owner asked for "my Claude Code does the job". Letting it
  skip the gates would make Done mean less than it does for the daemon.

**D8. The studio starts and stops a project's daemon.** The Board's **Run** and **Stop** buttons
start `operonx-project daemon` detached. Its pid goes in `.operonx/project/daemon.pid`, and the
store's lease already prevents two daemons. Machine-local settings (the sandbox, model overrides)
go in `.operonx/project/settings.toml`, outside git: whether bubblewrap works is a fact about the
machine, not the project. An unsandboxed daemon shows a badge on the Board.

**D9. The assistant gets the board as tools** (`operonx_studio/mcp.py`):

| Tool | What it does | Mode |
|---|---|---|
| `board_status` | the milestones, columns, what waits on the owner | read |
| `board_card` | one card: plan, rounds, findings, its run | read |
| `board_start` | a milestone for rows (or a goal), from the checklist | edit |
| `board_act` | approve, answer, pause, resume, cancel, replan, retry, prioritize (the engine's `api`) | edit |
| `board_run` / `board_stop` | the daemon (D8) | edit |
| `board_take` / `board_submit` | the assistant works one card itself (D7) | full |

Each tool also opens the Board tab beside the chat, the way `open_run` opens a run. The
assistant's knowledge page (`knowledge.md`) gets one section on the two layers and these tools.

**D10. "Realtime" is the event cursor, plus a live line per card.** The Board already redraws when
the cursor moves (3 s poll). New: the coder's current step (tool and file, for example "editing
`src/tip/ops.py`") is stored as a throttled `activity` event, at most one per card every 2 s, and
shown on the card's tile and in the assistant's turn. *Why:* polling is what the assistant and
playground use and it is proven here; server-sent events would be a second channel to secure.

**D11. A project in a subfolder is its own project.** The project root is the folder with
`operonx.toml`. Its state and BRD live there; git runs at the top level; branches are
`milestone/<slug>/<m>/main` when the root is not the top level; a card's files are relative to the
root. *Why:* meeting-prep's two builds share one repo, and the engine would otherwise mix them.

**D12. Existing projects are adopted, not rebuilt: `operonx-project adopt`.**
1. It writes `docs/brd/` where it is missing.
2. A read-only Claude Code session, which may write only under `docs/brd/`, drafts the rows from the
   project's docs, README and tests.
3. The work already built becomes a milestone `m0 · baseline`, state `done`, with one done card per
   feature. Open work (BACKLOG.md, plans in `docs/`) becomes rows `planned` / `not_implemented`.
   Those rows get cards when the owner starts a milestone for them; no cards are invented.
4. **The model never marks a row verified.** The engine runs each cited test, and only a test that
   exists and passes makes `implemented_verified`. Otherwise the row is `implemented_unverified`.

**D13. Usage docs: one source per topic, synced into every project** (§9). How to build with the
board is the operonx-project guide page. AGENTS.md carries a short marked block,
`<!-- operonx:builder -->`, which `operonx guide` rewrites in any project that has `docs/brd/`, so
projects made before this plan get the current text, the way the commands block works since 1.31.1.

**D14. The owner's words become rows: `board_request`.** The assistant saves the owner's message as
an owner request, `docs/brd/sources/owner_requests/<date>-<slug>.md`, registered as a source (role
`approved`, the owner's own words), and runs the extraction on it (`brd extract`, as for any
source). The new rows land `planned`, and a milestone for them is started with its plan waiting
for approval. *Why:* every row keeps a source, even one that came from a chat line. An idea the
owner is still discussing stays a chat; only "do it" becomes a request.

**D15. Ship from the Board, only on the owner's tap.** When a milestone is delivered, the Board
offers **Ship**. It pushes the milestone's branch and opens the PR: through `gh` for GitHub, and as
a pre-filled merge-request URL for GitLab, which has no API token here. **Merge** is offered for a
repo whose owner has granted it in the repo's settings, and runs after the PR's checks are read,
never before. A release (a version bump, a changelog) is a card inside the milestone, so it is
built and checked like the rest. *Why:* the engine never pushes (P1's rule), and the owner keeps
the one irreversible step.

**D16. The owner's standing rules reach every coder.** There are two files:
- `~/.operonx/owner.md`: the owner's rules for all projects, for example no co-author trailer,
  pull before every commit, never touch the OneWealth checkout, callbot work only on its branch.
  It is seeded from the rules the owner gave in editor sessions.
- The project's own `AGENTS.md`.

The engine appends both to every coder session's system prompt (`--append-system-prompt`), and a
reviewer checks a diff against them. *Why:* a daemon's coder works in a worktree under
`~/.operonx/worktrees`, where no editor memory is loaded. Today it would not know any of these
rules.

**D17. Work across repos: linked milestones.** A milestone may declare that it needs another
project's milestone (`needs = ["operonx-studio#m3"]`). It waits, state `waiting_dependency`, until
that one is delivered, and both Boards show the link. The studio's home lists every project's board
with what waits on the owner. *Why:* much real work changes operonx and the studio together, and
one board per repo stays the rule.

**D18. The owner is told when something waits.**
- The studio polls one cheap route (`/api/boards/waiting`) across projects.
- The count shows in the tab title and on the home page.
- A browser notification fires for each new question while a studio tab is open, foreground or not.

Push to a closed phone is Telegram's job (P10). *Why:* Web Push needs a service worker and keys
for one user, which is more than this needs.

---

## 3. The board files

`docs/brd/board/README.md`, the index:

```markdown
# Board

| Milestone | Title | State | Rows | Cards | Done | Branch |
|---|---|---|---|---|---|---|
| m0 | baseline | done | 14 | 6 | 6 | main |
| m1 | English calls | running | 3 | 4 | 1 | milestone/m1/main |
```

`docs/brd/board/m1.md`, one milestone:

```markdown
# m1 · English calls

Goal: score English calls with the same seven cases. Rows: QC-REQ-LANG-01..03. Mode: w2.
State: running. Plan: approved 2026-10-20 (v2, 1 review round). Budget: $30, spent $4.10.

| Card | Title | Rows | Deps | Kind | Status |
|---|---|---|---|---|---|
| C1 | language detect | QC-REQ-LANG-01 | | code | done |
| C2 | English prompts | QC-REQ-LANG-02 | C1 | code | in_progress |

### C1 · language detect

- Intent: tag each call vi/en before the cases run.
- Criteria:
  - When a transcript is English, the flow shall tag it `en`. (QC-REQ-LANG-01)
- Files: `src/qc/lang.py`, `tests/unit/test_lang.py`
- Outcome: done 2026-10-20 · tests `tests/unit/test_lang.py::test_tags_english` · run `r-8f2…` · $0.62
```

The engine's writer and reader own this format. A person may edit a card's intent or criteria
before a milestone is approved; after that, a change goes through `replan` so the store and the
file never fork.

---

## 4. The lifecycle with both layers

```
new feature ─► rows added to the checklist (planned)
            ─► start a milestone for those rows ─► plan ─► approve ─► cards on the board (Backlog)
            ─► daemon, or take/submit ─► In progress ─► Review ─► QA ─► Done   (board file updated)
            ─► milestone ends ─► rows implemented_verified ─► DELIVERY.md ─► the owner merges
clone / lost store ─► the board rebuilt from docs/brd/board/
rebuild elsewhere ─► start --from-board m1 ─► same cards, pending ─► approve ─► …
```

---

## 5. Phases

Order: B1 → B2 → B3 → B4 → B5 → B6 → B7 → B10 → B8 → B9. Each phase is one branch per repo, its docs updated in the same branch, merged when its gate
passes. Tests run locally; no phase waits on CI.

### B1. The board in the BRD (operonx-project; operonx for `init`)

- `operonx_project/brd/board.py`: `write_board(store, brd, mid)`, `read_board(brd)`, the format
  of §3. Called at D4's moments by the lifecycle and the merge step.
- `Project.open`: rebuild the store from the files when `project.db` is missing (D5).
- `brd check`: the links of D3.
- `operonx init` (on top of `feat/init-brd`): `docs/brd/board/README.md` with an empty index; the
  `_brd` template's README and MAINTAINING describe the board layer.
- **Gate:**
  - the round-trip test: milestones, cards, states, rows, criteria, deps and outcomes are equal
    after store → files → empty store;
  - the demo project (`~/operonx-project-demo/proj`) gets its board files from its real m1;
  - deleting its `project.db` gives the same Board in the studio (screenshot).

### B2. A project in a subfolder (operonx-project)

- D11: the project root, `milestone/<slug>/<m>/main`, paths relative to the root.
- **Gate:** a test with two projects in one repo. Each has its own board and BRD, their branches
  never collide, and a card of one cannot touch the other's files.

### B3. Take and submit, and the live line (operonx-project)

- `take` / `submit` in the CLI and in `api` (D7); a taken card has a lease, so the daemon never
  takes it too, and the lease ends after 2 h of silence.
- `activity` events from the coder's stream (D10), throttled.
- **Gate** (a scripted coder, no model):
  - take C1 → In progress, actor `assistant`;
  - submit with a failing held-out test → back to In progress with the finding;
  - submit passing → merged, Done, board file updated;
  - activity at most one per card per 2 s under a stream of 100 tool events.

### B4. Adopt (operonx-project)

- `operonx-project adopt [--repo .]` (D12): BRD, rows, `m0 · baseline`, open work as rows.
  Rows are verified only by running their tests.
- **Gate:**
  - on a fixture repo (3 features with tests, one failing, one BACKLOG item), the right rows are
    verified, unverified and planned, and `m0` is done with 3 cards;
  - the session cannot write outside `docs/brd/`: an attempt is refused in the test.

### B5. The studio: Board, daemon, assistant tools (operonx-studio)

- Merge `feat/board-project-checklist` into this phase's branch.
- Board:
  - Run / Stop (D8) and the unsandboxed badge;
  - "Build these rows" from the Checklist (a selection → `board_start`);
  - the live line on tiles (D10);
  - the board files' history: a card links to its section in `docs/brd/board/`.
- Assistant: the tools of D9 and a knowledge section; read mode gets only the read tools.
- Screenshots on desktop and phone after each change (the UI rule).
- **Gate:**
  - studio tests for each route and tool, with a fake engine;
  - one real assistant turn ("what is on the board?") that answers from `board_status`.

### B6. The docs sweep (all repos; §9)

- **Gate:**
  - `tests/guide` passes (every snippet runs);
  - `operonx guide` on a project made with 1.31.1 adds the builder block and changes nothing else;
  - `grep` finds no page that calls `.operonx/project/` the plan, or `project.db` the board.

### B7. A toy project, end to end (paid: the owner's Claude Code sign-in)

`tipcalc`, made with `operonx init`: a service that splits a bill, five rows (split evenly, a tip
percentage, rounding, a currency, a refusal for a negative bill).

1. From the studio assistant: "build the open rows". It calls `board_start` and `board_run`, and
   the Board moves while the cards run. Screenshots at Backlog, mid-run and Done.
2. One card done by the assistant itself (`board_take` → its edits → `board_submit`), with one
   deliberately failing submit first.
3. Delete the store and reopen it: the board comes back from `docs/brd/board/`.
4. `start --from-board m1` in a fresh `operonx init` repo: the same cards, built again.

- **Gate:**
  - all five rows `implemented_verified` in both builds;
  - the same cards and deps;
  - a card's move shows on the Board within one poll (3 s) of its event, read from the event's
    time and the Board's request log;
  - the cost of each run, reported.
- Cap: `--max-usd 5` per milestone.

### B8. Adopt the four studio projects

`adopt` on each, then each BRD read by hand against its code and tests before it is committed.
Corrections are made by hand, and each one is noted in that project's `docs/brd/CHANGELOG.md`.

| Project | Repo | Lands as |
|---|---|---|
| qc-snatcher | `qc-snatcher` (GitHub) | a branch, PR, merged |
| meeting-prep-operonx | `meeting-prep-projects` (GitHub, subfolder, B2) | one PR for both |
| meeting-prep-brd | same repo; `REBUILD_PLAN.md` §1 (R1–R10) is its source | same PR |
| educa-reminder-agent | GitLab, `refactor/operonx-studio` | commits on that branch only; the untracked files there are never added |

- **Gate:**
  - each Board shows `m0 · baseline` done and its open rows;
  - every `implemented_verified` row cites a test that passed in that adoption's run;
  - screenshots of all four.

### B10. The studio as the owner's console (operonx-project, operonx-studio)

- D14:
  - `board_request` (assistant tool), `operonx-project request TEXT` (CLI), and a "New request"
    box on the Board;
  - the extraction runs on the request, and the milestone is started for the new rows.
- D15: Ship (push and PR, or the GitLab URL) and Merge (where granted) on a delivered milestone,
  through `operonx-project api`, with the result on the Board.
- D16:
  - `~/.operonx/owner.md`, seeded from the owner's editor rules;
  - the coder appends it and `AGENTS.md`;
  - the reviewer's checklist cites it.
- D17: `needs`, the `waiting_dependency` state, and the links on both Boards; a "Boards" list on
  the studio's home.
- D18: `/api/boards/waiting`, the title count, and browser notifications.
- **Gate (on tipcalc, after B7):** the whole loop on the phone layout, with no editor:
  1. one chat message ("tipcalc: add a split by item");
  2. rows and a milestone appear, and the plan is approved with a tap;
  3. a question is answered on the Board;
  4. Done, then Ship opens the PR.

  Plus:
  - a coder session's prompt holds owner.md (read from its transcript);
  - a test with two linked projects shows the parent waiting and then running;
  - screenshots at each step.

### B9. Release

- operonx 1.32.0: `init` with `docs/brd/` and the board folder, `brd_files`, the builder block,
  the guide pages.
- operonx-project on Operonx main (installed from the repo, not PyPI).
- Studio PRs merged; the live studio on :8766 restarted; its public link checked.

---

## 6. What it costs

| Phase | Model use | Cap |
|---|---|---|
| B1–B3, B5–B6 | none in tests (scripted coders, a fake engine) | n/a |
| B4 | the fixture only | $1 |
| B5 | one assistant turn | $0.50 |
| B7 | two milestones of about five cards | $5 each |
| B8 | four adoption sessions | $2 each |
| B10 | one extraction and one small milestone on tipcalc | $3 |

The coders run on the owner's Claude Code sign-in, so the plan's rate limits apply. A rate limit
pauses the milestone, which resumes at the reset (built in P5).

---

## 7. Risks

- **The board file and the store disagree.** Only the engine writes either, and the round-trip
  test is the gate. A hand edit after approval is refused by `brd check` with the replan command
  to use.
- **Unsandboxed runs** (§10, the owner's choice): the coders in B7, B8 and B10 have this user's
  full shell. Their prompts carry owner.md's rules (D16), the reviewer reads every diff, and nothing
  is pushed without Ship.
- **Adoption overstates.** That is why D12's verified rows come from tests the engine ran, and why
  B8 reads every BRD by hand.

---

## 8. Not in this plan

- Telegram for real (P10: needs the bot token).
- The bake-off (P7).
- Replaying transcripts or answers beyond `open_questions.md`.

---

## 9. The usage docs

Every page that names init, the BRD or the builder, and what each must say after this plan. **One
source per topic; the others link.**

| Page | Repo | Change |
|---|---|---|
| `operonx_project/guide/01-project.md` | Operon | **The source** for building: the two layers, the board files, the lifecycle (§4), take/submit, `adopt`, `--from-board`, the studio's Run/Stop, settings.toml. Tested snippets |
| `operonx_project/guide/README.md`, `packages/operonx-project/README.md` | Operon | Point to 01-project.md; the README keeps a 6-line quick start |
| `operonx/cli/templates/_common/AGENTS.md.tmpl` | Operon | The `<!-- operonx:builder -->` block (D13): keep a row true when you change its code; to work a card, `take` it, and `submit` it when its criteria pass; never edit `docs/brd/board/` by hand |
| `operonx/guide/__init__.py` | Operon | Sync the builder block, as for the commands block |
| `operonx/cli/templates/_brd/README.md.tmpl`, `MAINTAINING.md.tmpl` | Operon | The board layer; who writes it |
| `operonx/guide/05-project-layout.md` | Operon | The tree with `docs/brd/` and `docs/brd/board/`, a link to the builder page |
| `operonx/guide/09-studio.md` | Operon | The Board tab and the assistant's board tools |
| `operonx/guide/README.md` | Operon | The index line for the builder |
| `README.md` (Operon) | Operon | The quick start: `operonx init` makes the BRD; one line on the builder |
| `docs/guide/00-installation.md` | Operon | `--no-brd` beside `--uv` / `--pip` |
| `docs/PROJECT_ENGINE_PLAN.md` §4, §12 | Operon | A note at the top of each: superseded by this plan for the board's place |
| operonx-studio `README.md`, `operonx_studio/knowledge.md` | studio | The Board (before and after a milestone, Run/Stop) and the assistant's tools |

Each phase updates the pages its change touches; B6 checks them all together.

---

## 10. The sandbox: answered

This host blocks bubblewrap (`apparmor_restrict_unprivileged_userns = 1`). The owner chose
(2026-10-19, "I don't need sandbox") to run the coders unsandboxed: `.operonx/project/settings.toml`
holds `sandbox = false` on this machine, and the Board shows the badge. No AppArmor change is made.
