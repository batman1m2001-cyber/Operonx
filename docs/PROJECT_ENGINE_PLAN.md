# operonx-project: an engine that builds software projects on its own

**Status:** 2026-10-17, v2: adds the living BRD (§4) and the workflow
bake-off (§5). Plan for review. Nothing is implemented yet. Every
"today" below was read in the code or measured on this machine.

**In one paragraph.** You give the engine your requirements, in any form
(a sentence, a BRD `.docx`, a zip of mockups), and a git repository. It
turns them into a **living BRD** in the repo with a traceability matrix,
the **checklist**, and runs the work as cards on a **board** (§4–§6), or
as a whole task graph planned up front, whichever the bake-off in §5
shows works better. Independent tasks run in parallel, each in
its own git worktree. A coding agent (headless Claude Code) implements
each task. A *different* session reviews the diff, and the repo's real
tests decide whether it passes. When it hits a decision it cannot make
safely, it messages you on Telegram. The task's run **parks** in the
journal (nothing waits in memory, no tokens burn), the other tasks keep
going, and your reply resumes that exact run in any process, even after a
restart. It ends with a branch, a PR description and the evidence behind
every claim.

**One example.** Goal: *"Build `leak-watch`: every morning it scans our git
repos for files that must not be public (archives, office documents,
`.env`, keys, big files), and a pre-commit hook stops them before they
leak. Our repos follow `policy/CONTRIBUTING.md`."*
1. The engine creates the project with `operonx init --template hello`,
   so it has the standard layout, `AGENTS.md` and the guide. It then
   writes `docs/brd/` in the repo:
   - the goal as source SRC-01, and the policy file as SRC-02;
   - 14 requirement rows;
   - `Q-01 (conflict)`: the goal says "flag office documents", but the
     policy commits `.docx` templates in `docs/templates/` on purpose.
2. It plans 6 cards. The rows of `Q-01` wait, while cards C1–C3 and C5
   move across the board.
3. You get *"Block all office files, or allow `docs/templates/`? I
   recommend the allowlist."* on Telegram, and reply "allowlist, but
   warn".
4. Card C4 resumes in the same Claude session with your answer. A
   reviewer session that never saw C4's reasoning checks the diff, pytest
   runs on real temporary git repos, held-out tests the implementer never
   saw run too, and so do the operonx convention checks.
5. C4's rows turn `implemented_verified` in the checklist.

---

## 1. What exists today (the evidence)

What I read and ran, and what each piece means for this engine.

**operonx core: durable runs (`operonx/durable`, guide page 08)**
- `Operon(journal=SqliteJournal(...))` journals each op's result and every
  cell it writes as the op ends. `engine.resume(run_id)` continues a
  stopped run in any process. What had ended is replayed, not run again.
- `InterruptOp` + a journal **parks** the run. The question is journalled,
  ops in flight finish, nothing new starts, and the run ends `interrupted`
  with `"$interrupted": [{interrupt_id, op, ctx, payload}]`. `resume(run_id,
  answers={id: value})` continues it later. The `interrupt_id` is
  deterministic: a hash of run, op and ctx.
- `resume(retry_failed=True)` re-runs from the first failed op. `fork(run_id,
  at=, outputs=)` exists. `handle.drain()` stops a run cleanly for a deploy.
- **Measured here (probe, scratchpad `probe_park_loop/probe.py`):** an
  `InterruptOp` inside a loop (`work → ask → keep → work`) parks once per
  iteration with a distinct question. Three resumes, each from a fresh
  `Operon` on the same `SqliteJournal` (as a new process would be), finish
  the run. Every completed `work` iteration ran **exactly once**
  (`RAN == [0, 1, 2]`). One caveat: ops inside a loop do not surface their
  outputs as the run's outputs; only declared cells do. The task flow
  writes its outcome to a cell.
  A second probe (`probe2.py`) ran **the exact §7 shape**: `round_` loops
  back to itself on `fix`, parks on `ask` (then `keep` loops back to it),
  and ends otherwise. The script was fix, fix, ask, fix, ask, end. It
  parked twice, and three engine instances finished it. `prepare` and
  each of the six rounds ran exactly once, and the answers reached later
  rounds in order (`[] → ['a0'] → ['a0', 'a1']`).
- Cost (guide 08, measured in #162): about 3.8 ms per run with a SQLite
  journal. That is irrelevant at minutes per task.

**operonx core: Jobs, queue, serve**
- `Job` keeps per-item records and tags traces with
  `metadata={"job", "job_run", "key"}` (`app/jobs/runner.py:203`). You decided
  in JOB_PLAN that a composition of jobs is plain asyncio above the engine,
  "NEVER a graph of jobs", and that jobs are records, not spans. The task
  DAG follows the same rule.
- `RunQueue` / `SqliteQueue` (`app/queue.py`): `put / claim / renew / finish`
  with leases, ordering per thread and counts. It fits the inbound
  commands (answers, pause, cancel) as they are.
- `webhook(...)` doors, `queue=`, `callback_hosts`, `Application(middleware=)`
  and `ResourceHub.guard` exist. A Telegram webhook would be an ordinary
  `Service`.
- Retries (`Retry`, `retry_call`, the `TRANSIENT` classifier), `Timeout`,
  `run_context().idempotency_key`, `child()` steps, `measure()`, and live
  traces in SQLite or ClickHouse are all there.
- There is **no dynamic DAG**: graphs are static, module-level `@graph`s.
  A plan generated at runtime cannot *be* an operonx graph. That is the
  one thing this package must add.

**operonx-agents (`packages/operonx-agents`)**
- `Agent`/`Runner`, `@tool`, `ToolPolicy`, approvals that park in a
  `StateStore`, `AgentOp`, `redact`, `UsageLimits`, trajectory evals.
- It has **no file or shell toolset**, and the in-house vLLM has no tool
  calling (memory: brd AgentOp). As a coding agent it would mean building
  Claude Code's tools again. I reuse its `redact`. A second coder backend
  on it (any OpenAI-compatible model, via OpenRouter) is later work.

**operonx-studio (separate repo, vanilla-JS UI)**
- It never imports a project. It reads the records on disk (runs, job
  records) and acts through a JSON-lines bridge under the project's own
  interpreter (`studio/play.py`, `operonx.app.play`: `parked`, `answer`,
  `retry`, `fork`).
- **It already drives headless Claude Code** (`assistant.py`, `chat.py`):
  binary discovery (including the VS Code extension's bundled binary),
  stream-json parsing with a 16 MB line limit, `--resume`/`--fork-session`,
  per-user sign-in through `CLAUDE_CONFIG_DIR`, environment scrubbing
  (`base_env`), reach modes (read / edit / full via `--allowedTools`), and a
  git snapshot, diff and undo for every turn. That is proven code to reuse.
- It has Runs with a "Waiting for an answer" filter and a question card,
  live runs, run pages with a workflow view, Compare → Code (git diff),
  Jobs and Monitor.
- **Name collision:** the studio ships an import package called
  `operonx_project` (manifest, extract, lint, scaffold) and installs it
  into target projects' environments. A new `operonx_project` module would
  clash with it. See decision D1.

**Claude Code here**
- Version 2.1.300 is signed in (claude.ai, **Max** subscription). This was
  checked with `claude auth status`, which is free.
- Headless flags that matter: `--output-format stream-json`, `--json-schema`
  (validated structured output), `--max-budget-usd`, `--model`, `--effort`,
  `--resume <id>`, `--fork-session`, `--allowedTools` / `--disallowedTools`,
  `--permission-mode`, `--add-dir`, `--settings`, and `--restricted` (drops
  Bash and the other code-running tools unless `--tools` names them, ignores
  user and project settings, confines the file tools to the working dirs,
  and refuses `bypassPermissions`).

**Machine**
- 24 cores, 47 GB RAM, about 20 GB free. Docker is available. **No
  `bubblewrap`**, so Claude Code's own bash sandbox cannot run (D2).
- The **staging callbot runs on this box**. Parallelism stays modest
  (default 2 coders).

**Messaging**
- There is no Telegram (or Slack) code anywhere in Operon, the studio or
  agents.
- Telegram Bot API facts: updates come by `getUpdates` long poll (one
  outstanding request, up to about 50 s, no public URL needed) or by
  webhook (needs public HTTPS). The two are mutually exclusive. Updates
  are held 24 h until acknowledged by `offset`. `callback_data` is at most
  64 bytes. Button presses arrive as `callback_query` with the original
  message. A reply carries `reply_to_message`.

**Outside practice (research)**
- Anthropic's harness for long-running agents keeps progress in durable
  artifacts (a feature list, a progress file, git history) rather than in
  one context window, and marks a feature as passing only after
  end-to-end verification.
- Industry write-ups converge on a split of planner, implementer and
  reviewer, wrapped in tests, permissions, audit logs, rollback and human
  review.
- The engine adopts the parts with evidence behind them: progress lives
  in a store and in git, and "done" means verified by commands, not by
  the agent's word.

### What this means

| Need | Reuse | Add |
|---|---|---|
| Durable waiting for a human | `InterruptOp` + journal park/resume | nothing in core |
| Crash recovery of a task | journal replay; `idempotency_key` | Claude session id recorded at `init` |
| Retries, timeouts | `Retry`, `Timeout`, `retry_call` | the engine's own rules per round (§7) |
| Durable inbound commands | `SqliteQueue` | — |
| Traces and inspection | run stores, studio run pages | `metadata={mission, task, round}` |
| Coding agent | studio's Claude Code plumbing (ported) | a `Coder` protocol, a structured-output schema per role |
| **Dynamic task DAG** | — | **a scheduler above the engine (new)** |
| **Mission state** (tasks, attempts, findings, interactions) | SQLite (as journal, queue and run store already use) | **a mission store (new)** |
| **Telegram** | `retry_call`, the redactor | **a messenger + Telegram adapter (new)** |
| **Requirements from any input; traceability** | OneWealth's `docs/brd` layout, rules and checker, as the model | **readers, extraction checks, a generalized template and checker (new)** |
| **Dashboard** | studio tab conventions, run pages, diff view | **a Missions tab with board, checklist and graph (new)** |

No new database server, queue, service or language is needed.

---

## 2. Decisions

### Made, with the reason

1. **Where it lives:** `Operon/packages/operonx-<name>` (D1 picks the
   name), published like agents and kb (`publish.yaml` matrix, its own tag
   prefix). The UI is a tab in the **operonx-studio** repo. Both were agreed
   in this session.
2. **A mission is a record, a task attempt is a run.** The engine keeps a
   mission store (SQLite). Each stage that does work is one journalled
   `Operon` run of a static, module-level graph. The DAG scheduler is
   asyncio above the engine, the same rule as Jobs. This gives one trace
   per task attempt rather than a single 6-hour trace, and gives the
   studio the run pages, journal answers, retry and fork for free.
3. **"Mission", not "project".** In this ecosystem, *project* already
   means a folder with an `operonx.toml`, and the studio's Projects home
   lists those. One goal on one repo, taken from plan to delivery, is a
   **mission**. Its parts are **tasks**, and a task's work happens in
   **rounds**.
4. **The coding agent is headless Claude Code** behind a `Coder` protocol.
   It is the only available agent with real file, search and shell tools
   and strong code models. The studio already runs it, and it is signed
   in here. A backend on operonx-agents is a later phase (§15).
5. **Independence of review and QA, by construction, not by prompt:**
   - **Different session and different model.** By default the
     implementer is `opus`, the reviewer `fable`, and planner and triage
     `sonnet`, all configurable. The reviewer never sees the implementer's
     transcript or self-assessment. It gets only the task, the acceptance
     criteria, the diff and the check results.
   - **The reviewer is read-only:** `--restricted`, file tools only.
   - **Acceptance tests are written before the code**, by a separate test
     author session, in paths the implementer may not edit. A diff that
     touches them is rejected mechanically.
   - **The verdict comes from commands.** The repo's own check commands
     (tests, lint, types) and the acceptance tests run in the worktree;
     exit codes decide. The agent's "done" is never the evidence.
   - **A finding must point at the diff.** A blocker or major finding has
     to cite a file in the diff and a line that exists. If it does not,
     it is kept as *unverified* and does not block.
6. **Parallel tasks never share a working tree.** Each task gets a git
   worktree on `mission/<mission>/<task>`, branched from the integration
   branch once its dependencies have merged. Tasks whose declared `paths`
   overlap do not run at the same time. Merges into the integration branch
   take one lock and are followed by the full checks (regression against
   the baseline).
7. **Waiting costs nothing.** A parked run holds no process, model
   session or token. The Claude session id sits in the store, and the
   answer resumes it (`--resume <id>`), so the agent continues with its
   whole context and the worktree as it left it.
8. **Answers have exactly one authority.** Every answer, from Telegram,
   the CLI or the studio, goes through one function,
   `submit_answer(interaction, answer, actor, via)`. In one transaction it
   checks the actor, the status (`pending`) and the context (§9.5), then
   puts a `resume` command on the queue. The engine journals its task
   runs in its own journal, not the project's `[durable]` one, so the
   studio's generic "Waiting" card cannot answer around these checks.
9. **Telegram is a mock first** (your instruction). The mock is an
   in-process **fake Bot API server** speaking the real HTTP methods
   (`getUpdates`, `sendMessage`, `editMessageText`, `answerCallbackQuery`).
   The real Telegram adapter is the code under test against it, so going
   live means changing only the base URL and the token, in the last phase.
   Tests that use the fake are labelled as such and are never cited as
   proof of the live integration.
10. **Long poll by default, webhook as an option.** `getUpdates` with
    `timeout=50` is one outstanding HTTP request that Telegram answers
    when a message comes. It needs no public URL and spends no tokens.
    This is not the "loop that re-polls and resubmits" that the brief
    rules out: no question is re-sent and no model runs while waiting.
    When the deployment has public HTTPS (your ngrok domain, say), the
    same handler mounts as a `webhook(...)` service instead.
11. **Nothing leaves the machine without approval.** Delivery is a local
    integration branch plus a PR description. `git push`, opening a PR,
    merging into a base branch and deleting branches are each an
    *approval* interaction unless the mission's policy pre-authorizes them.
    Telegram can approve what the policy allows and nothing more.
12. **Plan approval: one tap by default.** After the plan passes its
    review and the mechanical checks, you get it on Telegram once
    (approve / revise with instructions / reject). `approve_plan = "auto"`
    skips the tap when the plan review passed and no task is marked risky.
    There are no other gates by default.
13. **Delivery state is kept in git.** Each round that changes code ends
    in a commit on the task branch, with the mission, task and round in
    the message, so the git history is part of the record and partial
    work survives anything.

14. **Requirements are a living BRD in the target repo** (§4), with
    OneWealth's layout and rules generalized. A mission's definition of
    done is its matrix rows verified, not its tasks finished.
15. **The workflow is chosen by a bake-off, not by taste** (§5). One
    engine with policy knobs runs both candidates, so the comparison
    costs runs, not a second implementation. Both modes stay if each wins
    on a different kind of input.
16. **Quality is measured on held-out tests.** The bake-off measures it
    that way, and V2 makes it part of the engine if it earns its cost.
    Visible tests alone over-report (SpecBench).
17. **"Card" is the board's name for a task.** The two words mean the
    same thing in this document.

### Decided on your go-ahead (2026-10-17: "you decide yourself")

- **D1. The name stays `operonx-project`.** It imports as
  `operonx_project`, and its CLI is `operonx project <cmd>` (handed off by
  the operonx CLI, the way `operonx studio` is) plus an `operonx-project`
  script.

  The clash is fixed on the studio's side, by **splitting** the studio's
  module of the same name. That module was once meant to be a headless
  `operonx-project` distribution (the studio's docstring still says it
  "reads the Project IR produced by `operonx-project`"), and it is
  injected into every project's interpreter (`daemon.py:217-242`), so two
  `operonx_project`s would shadow each other. Its 4,081 lines split by
  where each part belongs:

  | Part | Lines | Goes |
  |---|---|---|
  | `manifest.py` | 450 | **replaced** by core's `operonx.app.manifest`; its extras (`resources.base`, `[graph.bind]`) are used by none of the 40 `operonx.toml` on this machine |
  | `scaffold.py`, `templates.py` | 819 | **replaced** by `operonx init`; the scaffold is an old layout (pinned at 1.5.0, `[[serve]]`), the starters are pinned at 1.17.1. A starter that core lacks moves into `operonx init --template` only if it builds on current operonx |
  | `lint.py`, `buildcheck.py` | ~500 | **moved to core** as `operonx lint` (§6.1) |
  | `extract.py`, `pyedit.py`, `edit.py`, `apply.py` | ~2,000 | **stay** (canvas IR, typed edits from the UI), renamed `operonx_studio_toolkit` |

  It is **not merged** into the engine. That would make the engine carry
  the canvas code and the studio depend on the engine. After the split,
  the engine needs only core.

- **D2. Install `bubblewrap` in P2,** when the implementer first runs, so
  that Claude Code's bash sandbox confines the implementer's shell to its
  worktree with no network. It is one system package, `apt install
  bubblewrap`. If the sandbox fails on this kernel, the fallback is the
  mitigations listed in §11, with the docs saying plainly that it is not
  a boundary. Check commands run in Docker either way.
- **D3. The Max login, with `max_parallel = 2`.** The engine pauses on a
  rate-limit reply and resumes at the reset time. The bake-off (P7)
  needs about 8–12 coder hours, so it runs in windows you are not using,
  overnight.
- **D4. Telegram at the end (P10), by long poll.** The bot token and your
  Telegram user id will be needed then. A webhook through the ngrok
  domain stays an option. Nothing before P10 needs them.
- **D5. OneWealth is test input M** and the E0 source. It runs on a
  local copy that is never pushed, and the private `.docx` is read
  locally and never committed (ADR-0001).

---

## 3. Architecture

```
            you ── Telegram ──┐          studio Missions tab ──┐        `operonx project` CLI ──┐
                              ▼                                ▼                    ▼
                    ┌───────────────────── commands (SqliteQueue) ─────────────────────┐
                    │  submit_answer · pause · resume · cancel · approve · revise      │
                    └──────────────────────────────┬───────────────────────────────────┘
                                                   ▼
   ┌───────────────────────────── project daemon (one per workspace) ─────────────────────────────┐
   │  MissionRunner: lifecycle (§6) · Scheduler: DAG, waves, locks, budgets (§8)               │
   │  Messenger: outbound questions, inbound updates (§9) · Recovery on start (§10)             │
   └───────┬──────────────────────────────┬──────────────────────────────────┬──────────────────┘
           │ one journalled Operon run     │ git worktree per task            │ reads / writes
           ▼ per stage / task attempt      ▼                                  ▼
   discover_flow · plan_flow ·      <repo>/.git  ── mission/<m>/<task>    missions.db (mission store)
   task_flow · integrate_flow ·        integration: mission/<m>           journal.db (its own)      
   final_flow                                                           .operonx/runs (traces)
           │ each round's stages call
           ▼
   Coder (ClaudeCodeCoder: headless `claude -p`, stream-json, --json-schema, --resume)
   Checks (subprocess or docker: the repo's own test / lint / type commands)
```

**The workspace** is a folder made by `operonx project init`. It holds an
`operonx.toml`, so the studio opens it like any operonx project, and it
has `.operonx/project/{missions.db, journal.db, missions/<id>/…}`. The **target
repo** is anywhere. A mission names it, and its worktrees live under the
mission folder.

**Package layout** (`packages/operonx-project/operonx_project/`):

| Module | What |
|---|---|
| `store.py` | the mission store: schema, transitions (one function per transition, checked), events |
| `model.py` | `Mission`, `Task`, `Round`, `Finding`, `CheckResult`, `Interaction` dataclasses and their state enums |
| `dag.py` | validation (acyclic, deps exist, coverage), waves, ready set, blocked propagation, path locks |
| `board.py` | columns, WIP limits, pull and push policies, the replenisher's trigger (§8.5) |
| `brd/` | readers (`.docx`, PDF, images, zip, text), the source register, extraction checks, `Q-nn`, the template, `operonx project brd init / check` (§4) |
| `examiner.py` | held-out tests: written outside the repo, run at card done and at the end; failures reported by name and message only (§5.4) |
| `scheduler.py` | the asyncio loop: start ready tasks under limits, react to run ends and commands |
| `flows/` | the module-level graphs: `discover_flow`, `plan_flow`, `task_flow`, `integrate_flow`, `final_flow` |
| `coder/` | `Coder` protocol, `ClaudeCodeCoder` (ported from the studio), role prompts and JSON schemas |
| `checks.py` | run declared commands (host or docker), with a timeout, exit code and output tail; baseline diff |
| `gitops.py` | worktrees, branches, commits, merges under a lock, conflict detection, diff for review |
| `interact/` | `Interaction` lifecycle, `submit_answer`, escalation triage, message rendering, expiry |
| `messengers/` | `Messenger` protocol, `TelegramMessenger`, `FakeBotAPI` (test server), `ConsoleMessenger` |
| `daemon.py`, `cli.py` | `operonx project init / new / up / status / pending / answer / pause / resume / cancel / show` |
| `guide/` | the package's tested guide page, like agents and kb |

---

## 4. Requirements: a living BRD

Requirements are not a planner's scratch list inside the engine. They are
a **folder in the target repo**, versioned with the code, that every
mission on that repo reads and updates. The model for it is OneWealth's
`docs/brd/`, which was built by hand over seven plans (P0–P7, N, K, R, F,
G, A; 46 commits). In that folder:
- an immutable source register with hashes;
- requirements extracted with their original wording and location;
- feature (behaviour) and UX layers;
- open questions of four types;
- ADRs;
- one traceability matrix where status lives, whose
  `implemented_verified` needs a cited passing test;
- a checker (`scripts/check_brd_docs.py`) that runs in the test suite.

At the end of G9 it traced 252 rows. The engine makes that discipline
automatic and general.

### 4.1 Input in any form

| Input | How it is read |
|---|---|
| a goal in a sentence, a chat message | kept verbatim as a source (`sources/owner_requests/<date>_<slug>.md`) |
| `.docx` | the standard library (`zipfile` + the document XML): text with headings, tables, and embedded figures saved as images. There is no pandoc or LibreOffice on this machine, and none is needed |
| `.pdf`, images (mockups, screenshots, photos of whiteboards) | Claude Code reads both natively; `tesseract` (installed) for scanned pages whose text is needed for quoting |
| a `.zip` kit | extracted into its own empty folder as untrusted data, then each file by its type |
| Markdown, plain text | as is |

**The source register** (`sources/README.md`): each source gets `SRC-nn`,
its kind, where it lives, its SHA-256, and its role: *authoritative*,
*approved* (you asked for it) or *proposed* (a kit, a suggestion).
Sources are never edited. A new version is a new row.

A source marked **private** (your BRD `.docx`) is identified only by path
and hash, and is never committed: this is OneWealth's ADR-0001, and the
lesson of the leaked zips. The engine adds it to `.git/info/exclude` and
refuses a commit that contains it (the same guard as §7, step 3).

### 4.2 Extraction, checked mechanically

The requirements session (`brd_flow`) writes requirement records:
- an ID that keeps the source's own codes (`BR-09` → `BRD-BR-09`) or gets
  a prefix per source (`OWN-`, `KIT-`, …);
- the **quote in the original language** and its location (section,
  table, figure, page);
- a type (business, functional, non-functional, rule, KPI) and a
  documentation status (`documented`, or `needs_clarification` with a
  `Q-nn`).

Then the engine checks what an agent tends to get wrong:

- **Quotes are real.** Each quote must occur in the source text, after
  normalizing whitespace and punctuation. If it does not, the record is
  sent back. A paraphrase is allowed only when marked as one, as
  OneWealth's rules table does.
- **Nothing is skipped.** Every section of the source yields at least one
  requirement, or is marked *descriptive* with a reason. The engine
  computes this from the section list it extracted itself, not from the
  agent's word.
- **IDs are stable and unique.** A retired ID is marked `superseded` and
  never reused.
- **A second session hunts for misses.** It is given the source and the
  extraction and asked only for requirements that are missing. Its finds
  must quote the source too.

**Depth scales with the input.**
- A one-sentence goal gets `requirements.md` (five to fifteen `R-` rows)
  and the matrix.
- A real BRD gets the three layers (requirements, features, UX),
  architecture notes and ADRs.

  The template is OneWealth's layout, generalized (`operonx project brd init`), with
  its `MAINTAINING.md` rules.

### 4.3 Questions are `Q-nn`

Every open question in the folder has a type:
- **ambiguity**: a source is unclear;
- **conflict**: two statements disagree;
- **divergence**: the product differs from a source, knowingly;
- **gap**: required, not built, found while documenting.

Each also separates *fact* (with a source) from *interpretation* (ours,
proposed), and says who can decide. These are the escalation categories
of §9.1, so a Telegram question *is* a `Q-nn`. Your answer becomes its
resolution, and an ADR when it shapes the design. A question is never
deleted and never silently resolved.

The leak-watch conflict in the first example is `Q-01 (conflict)`: the
goal's "flag office documents" against the policy's committed templates.

### 4.4 The checklist is the traceability matrix

There is one row per requirement:

```text
requirement ─▶ feature ─▶ screen ─▶ code path / API ─▶ test ─▶ evidence
```

The status uses OneWealth's vocabulary:
- `implemented_verified`: at least one test cited by name, existing, and
  passing in the recorded run;
- `implemented_unverified`;
- `partially_implemented`, with the gap stated;
- `planned`, with the phase or card;
- `not_implemented`;
- `blocked`, with what blocks it and who can unblock it;
- `not_applicable`, with why.

This matrix is the **definition of done** for a mission:
- the mission's target rows end `implemented_verified`, or end in a
  status with the reason stated;
- the checker (`operonx project brd check`, generalized from `check_brd_docs.py`:
  links, IDs, coverage, cited tests exist and passed) is one of the QA
  commands of every card;
- a card's done includes its **doc obligations** from `MAINTAINING.md`:
  - the feature file;
  - the matrix row;
  - screenshots for UI;
  - a changelog line;
  - an ADR for a significant decision.

  The reviewer checks them like code.

### 4.5 Change, and the next mission

- **A new version of a source.** It is registered as a new row. The
  extraction runs on the difference, and the rows that changed are marked
  in the matrix. Cards for those rows re-open; done rows whose
  requirement did not change stay done.
- **The next mission on the same repo** starts from the matrix: the rows
  that are not `implemented_verified` are its candidate scope, and the
  folder is its memory.

  This is how OneWealth's G-plan started from the 15 rows F1–F10 had left.

---

## 5. Which workflow: candidates, and a bake-off to choose

Your flow is **create BRD → checklist → kanban board**. It is one of a
few serious ways to run agents from requirements to code. Since these
differ in ways that cost or save hours per mission, the engine should
choose between them by measurement, not by taste. This section is the
theory. Phase P7 (§14) is the measurement.

### 5.1 What was researched

- **GitHub Spec Kit** (constitution → specify → plan → tasks →
  implement): each phase is a Markdown artifact that feeds the next.
  GitHub calls it an experiment, and an independent review says it gets
  you a clean spec and a first generation, then leaves you on your own.
- **Kiro** (requirements → design → tasks). Requirements are user
  stories whose acceptance criteria use **EARS** ("When ⟨event⟩, the
  system shall ⟨response⟩"; also state-driven, unwanted-behaviour and
  optional forms). The design comes from the code base, tasks are a
  checklist whose items go In progress → Done, and you approve each
  document.
- **BMAD** (analyst → PM writes a PRD → architect → the scrum master
  *shards* them into self-contained **story files** carrying context,
  constraints, tests and links back → dev, one story at a time → QA).
  In its guide a human approves each story.
- **Anthropic's long-running harness.** A feature list with every item
  failing at the start; one feature at a time; marked passing only after
  end-to-end verification; progress kept in a file and in git, not in
  context.
- **Agent kanban boards** (Vibe Kanban, Cline Kanban, agent-kanban,
  kanbots). Each card gets its own worktree and agent; the columns are To
  do → In progress → Review → Done; Review holds both "waiting for your
  approval" and "finished, to be reviewed". Some have dependency-aware
  scheduling; one merges automatically by default.
- **Evidence on planning horizon** (CAIS 2026, on data QA, not code).
  Full-horizon planning with **lazy replanning** matched step-by-step
  planning at **2–3× fewer tokens**; constant replanning makes agents
  oscillate. A PDDL study found step-by-step agentic planning only +3
  points at 5.7× the tokens. Its authors note that coding agents do have
  grounded signals (compilers, tests), which favours feedback loops
  *inside* a task rather than replanning the whole mission.
- **Evidence on tests** (SpecBench, 2026). Frontier agents saturate the
  **visible** tests while a gap on **held-out** tests of the same
  features persists, and the gap grows with code-base size (about 28
  points per tenfold). ImpossibleBench measures how often agents game
  tests that contradict the spec. Visible tests alone over-report quality.

### 5.2 The axes that matter

Each workflow is a choice on five axes:

| Axis | One end | Other end |
|---|---|---|
| A. where requirements live | engine-internal list | a living BRD in the repo (§4) |
| B. when work gets detailed | the whole DAG up front | coarse epics up front, cards detailed **just in time** |
| C. what starts work | push: a task starts when its deps are done | pull: a free slot takes the highest-priority ready card, under WIP limits per column |
| D. the unit of done | a task passed its checks | a **requirement row** is `implemented_verified` |
| E. what the verdict trusts | visible acceptance tests, protected from edits | plus **held-out** tests the implementer never sees |

### 5.3 Rejected in theory (not worth a run)

- **One agent looping over a checklist**, Ralph-style or the bare
  harness. It has no parallelism and no independent review, and the
  replanning evidence shows the oscillation risk. The harness's good
  ideas are kept: a failing-first checklist, and progress kept in files
  and git.
- **A human approves every story** (BMAD's guide, Kiro's per-document
  approval). It defeats autonomy. One plan tap is kept, plus questions
  only when §9.1 says so.
- **Role chains that hand off documents only** (MetaGPT-, ChatDev-style
  waterfall). They have no grounded feedback until the end, which is the
  opposite of what the evidence favours.
- **A design document per feature** (Kiro's `design.md` per spec). That
  is one architecture note per mission, plus ADRs. Per-card design lives
  in the card.

### 5.4 The candidates

**W1. Spec → DAG** (the first version of this plan; Spec Kit, Kiro).
- A1 internal, B whole DAG, C push, D task, E visible and protected.
- Requirements → one design note → the whole task DAG up front → tasks
  start as their dependencies merge → final verification maps tasks back
  to requirements.
- *Expected strength:* maximum parallelism, and the whole picture once,
  early. *Expected weakness:* plan errors found late; detailing tasks that
  later change.

**W2. BRD → checklist → board** (your flow, with BMAD's story cards and
the harness's failing-first checklist).
- A living BRD, B just in time, C pull with WIP limits, D requirement row.
- The steps:
  1. The living BRD (§4) is written.
  2. The **checklist** is its matrix, every row starting `planned`.
  3. A coarse plan groups the rows into epics, with the dependencies
     between epics and one architecture note.
  4. A **replenisher** keeps the Ready column topped up. It details only
     the next few cards (`ready_depth`, default 2 × WIP) as
     self-contained story cards: the rows they serve, criteria, context
     excerpts, paths, dependencies and doc obligations.
  5. Free slots pull the highest-priority ready card.
  6. A card is Done when its rows are `implemented_verified` and the
     checker passes.
  7. Replanning is lazy: what a finished card taught (a new constraint, a
     failed approach) reaches only cards not yet detailed.
- *Expected strength:* cheaper and more adaptive (the lazy-replanning
  evidence); progress is measured in requirements, not tasks; it is what
  a person running the project wants to see. *Expected weakness:* less
  parallelism early; architecture drift if the epic plan is thin.

**Verification variants, crossed with both:**
- **V1**: visible acceptance tests, protected (the first version).
- **V2**: V1 **plus held-out tests**. A separate *examiner* session writes
  a second test set from the same rows. It lives outside the repo, and
  neither the implementer, the reviewer nor the test author sees it. It
  runs at card done and at mission end.

  A card that passes its visible tests but fails held-out ones goes back
  with only the *names and messages* of the failures, never the test code.
  That lets an agent learn the behaviour without gaming the test.

### 5.5 One engine, not two

W1 and W2 differ only in policy, so the engine has one runtime with these
knobs:
- `horizon` (`all` | `ready_depth`);
- `start` (`push` | `pull`, with column WIP limits);
- `done_unit` (`task` | `row`);
- `heldout` (on | off).

Cards carry dependencies either way, and the board and the DAG are two
views of the same cards. W1 is `horizon=all, start=push, done_unit=task`;
W2 is `horizon=ready_depth, start=pull, done_unit=row`. A bake-off then
costs runs, not a second implementation. The result may well be "both,
by input": W1 for a small goal, W2 for a real BRD. The engine keeps both
as modes and picks the default from the input's size.

### 5.6 The bake-off (P7), designed before any run

The metrics are written down before any run, so the numbers decide
rather than whoever reads them. The rule I break otherwise is
[the benchmark must match the shape].

**Inputs**

- **S, small greenfield:** leak-watch (§13), created with `operonx init`. A one-paragraph goal, a
  seeded repo, one genuine conflict.
- **M, medium brownfield, a replay:** a finished OneWealth phase, for
  example F8 (newsletter, #48) or F7 (alerts, #47), picked by size in P7.
  The repo is checked out at the commit **before** that phase. The
  engine gets that phase's requirement rows and the BRD folder as it
  was, never the later code. The tests the phase actually added are
  removed from the checkout and become **held-out tests** written by a
  third party (the original work). This is SWE-bench's method on your own
  history, and it gives a known-good reference for scoring. The run uses
  a local copy that is never pushed (D5).

**Pre-registered metrics**, recorded per run:
1. **held-out pass rate**: the primary quality measure. For S, the tests
   are written by me and hashed before the first run; for M, the
   phase's real tests;
2. **requirement rows verified** (checker-confirmed) / target rows;
3. **cost**: notional USD, tokens, wall clock, coder minutes;
4. **your load**: questions asked, and how many of them were on the
   pre-registered list of genuine ambiguities in the input (wasted
   questions are counted against the workflow);
5. **rework**: rounds per card, re-opened cards, replans, reverted
   commits;
6. **escapes**: findings of an independent final review of the delivered
   diff (the same reviewer prompt for every run), and regressions against
   the baseline.

**Runs:**
- S: W1 and W2, two repeats each, with V2 on, so that held-out tests are
  measured everywhere;
- M: W1 and W2, one run each, with V2;
- the V1 vs V2 effect is read from the same runs. In V2, held-out
  failures that reached card done are the escapes that V1 would have
  shipped.

That is about 6 runs, roughly 8–12 hours of coder time spread over
several Max windows (D3).

**The decision rule**, fixed now:
1. The higher held-out pass rate wins, if the gap is ≥ 10 points on M or
   consistent across both S repeats.
2. Otherwise, lower cost at an equal or better rows-verified score.
3. Your load is a constraint: a workflow that asked you twice as many
   unnecessary questions loses a tie.
4. With N this small the result is *directional*. The report says so,
   keeps both modes, and lists what a larger run would need.

**Predictions** are written now so the result can surprise us:
- W2 costs fewer tokens and re-opens fewer cards on M;
- W1 finishes S sooner (more parallel early);
- V2 catches at least one escape on M that V1 would ship.

[the benchmark must match the shape]: a past mistake. I blocked a design
three times with numbers that measured something else. So every metric
here is measured on the real thing being decided.

---

## 6. The lifecycle

Everything below is common to both workflows, and §5.5's knobs decide the
differences. Each stage is adaptive: it is skipped or repeated by rules,
not mechanically.

1. **Intake.** The mission is created from its sources (any form, §4.1),
   the repo path, constraints, its policy (budgets, approvals, models,
   workflow mode) and its target scope (default: every row that is not
   verified). It is stored and nothing runs yet.
2. **Discover** (`discover_flow`, read-only coder). This writes the repo
   brief: stack, layout, conventions (it reads CLAUDE.md, AGENTS.md and
   CONTRIBUTING), and the commands to install, test, lint and type-check.
   **The commands are then run** to record the baseline: which checks pass
   *before* any change, so a test that was already failing never counts as
   a regression and is never blamed on a card. A check command that
   cannot be found or does not run is the first escalation candidate.
3. **Requirements** (`brd_flow`, §4). Sources are registered, requirements
   extracted with the mechanical checks, misses hunted, and questions
   written as `Q-nn` and triaged (§9.1):
   - *decidable*: decide it from the evidence and record the assumption;
   - *owner's, and it blocks everything*: ask now;
   - *owner's, and it blocks only some rows*: those rows wait as
     `blocked` with the `Q-nn`, and the rest proceeds.

   On a repo that already has the folder, only new or changed sources
   are processed.
4. **Plan** (`plan_flow`, `--json-schema`).
   - **W1:** the whole card DAG.
   - **W2:** epics over the rows, the dependencies between epics, one
     architecture note, then the first `ready_depth` cards.

   Each card has:
   - a title and intent;
   - the rows it serves;
   - acceptance criteria in EARS form, each tied to a row;
   - `deps`, and `paths` (the globs it may touch);
   - `kind` (code | test | docs | config);
   - `risk` (low | high: migrations, deletes, security, public API);
   - its doc obligations;
   - an estimate.
5. **Plan review.** A fresh session (reviewer model) checks for missing
   rows, untestable criteria, wrong dependencies, oversized cards, and
   overlapping paths that ought to be dependencies. The engine also
   checks mechanically:
   - the graph is acyclic and every dependency exists;
   - every target row is covered by at least one criterion;
   - paths are inside the repo;
   - at most `max_cards` cards are open at once (default 25).

   There are at most 2 revise rounds. After that, the plan with its
   unresolved review notes goes to you instead of looping.
6. **Approval** (one tap by default, §2 decision 12).
7. **Examiner** (V2 only). The held-out tests are written from the rows
   by a separate session into the mission folder, outside the repo,
   before any implementation.
8. **Execute** (§7, §8). Cards run, by push (W1) or pull (W2), and the
   replenisher details new cards as the Ready column drains (W2).
9. **Final verification** (`final_flow`):
   - the full checks on the integration branch against the baseline;
   - the held-out tests (V2);
   - `operonx project brd check`;
   - one review of the whole mission diff, looking at integration
     problems that no single card saw;
   - the matrix rows of the scope, each with its evidence.

   A gap re-opens the responsible card (bounded, §8.4).
10. **Deliver.** The integration branch with the updated BRD folder,
    `DELIVERY.md` (what was built, evidence per row, assumptions taken,
    `Q-nn` you decided, known limits, cost) and a PR description. A push
    or PR is an approval (§2, decision 11).

**Replanning.** A card that fails for good, a "revise" from you, or a
changed source can change the part of the plan that has not started
yet. That is at most 2 replans per mission, besides W2's ordinary
just-in-time detailing. Done cards are never redone. Questions on cards
that were replaced become `superseded` (§9.5).


### 6.1 A new project is an operonx project, made with `operonx init`

In this ecosystem a new product is an operonx project. So when the
mission is greenfield, the first card is fixed: **create the project with
`operonx init`**. The planner chooses the template from the requirements:
- `hello`: a job and an HTTP service;
- `http`: a doors graph;
- `chat`: one LLMOp;
- `agent`: operonx-agents.

It also chooses `--uv` or `--pip` (your rule: banks have no uv). The
engine runs the command itself; the coder does not hand-write the
layout. That gives every card the same footing:
- `AGENTS.md` / `CLAUDE.md` and `.operonx/guide/`, which Claude Code reads
  on its own, since its working directory is the worktree;
- the standard layout: `app/main.py` holding the `Application`, and
  `src/<feature>/graph.py` with `ops.py`;
- `operonx.toml`, `resources.yaml` and `.env.example`.

The role prompts add one line for operonx targets: *read
`.operonx/guide/README.md` and the page for any API before using it; never
write an operonx API from memory*. That is the rule `AGENTS.md` itself
states. On an existing operonx repo (OneWealth, the callbot), its
`AGENTS.md` is the convention, and `operonx guide --check` tells whether
its copy of the guide is stale.

**The conventions are checked, not hoped for.** For an operonx target
every card's QA runs:

| Check | What it enforces | Today |
|---|---|---|
| `pytest` | the project's tests and the acceptance tests | exists |
| build check: `operonx serve --list`, `operonx run --list`, and every graph built | the `Application` resolves, and every `@graph` builds (C1, C2) | exists (studio `buildcheck`) |
| `operonx lint` | op names from variables, no tuple unpacking (C3); no ops built or wired in loops (C5); resources by key, no credentials in code (C6) | exists as the studio's `operonx-lint`, **not on this machine's PATH** |
| `operonx lint`, new rules | no `@graph` inside a function (C7); no `print()` (C8); no retry loop in an op (C9); no logic, I/O or Python `if` in `graph.py` (C10); explicit `START >> … >> END` edges (C11); a new secret name missing from `.env.example` (C12) | **new**: the `AGENTS.md` rules a reviewer would otherwise have to remember |
| `operonx guide --check` | the project's guide copy matches the installed operonx | exists |
| the reviewer | the rest of `AGENTS.md`: one feature per folder, imports go one way, fake models in tests, `@op(retry=False)` for side effects | a checklist item in the review prompt |

**Where the linter lives.** The rules are core's: core's `operonx init`
writes them into `AGENTS.md`. The engine needs them in any target
environment, and today they are only in the studio repo. So P2 **moves
the static linter into core as `operonx lint`** (`lint.py` is
dependency-free AST code, about 330 lines) and adds C7–C12 with a failing
test first each. The studio's `operonx-lint` becomes a thin alias of it.
This is upstream work in core, done as its own PR, in line with "operonx
is upstream".

### 6.2 The engine follows the same conventions

operonx-project is itself operonx code, and it is held to the same
rules:
- its flows are module-level graphs, one feature per folder
  (`operonx_project/flows/<flow>/graph.py` + `ops.py`): wiring in
  `graph.py`, logic in `ops.py`;
- settings are graph inputs, and the edges are explicit;
- logging goes through `LOGGER`, never `print`;
- retry comes from `Retry`/`retry_call`, never hand loops, and side
  effects (a commit, a merge, a send) are `@op(retry=False)`;
- models are resources by key.

`operonx lint` and the build check run on the package in CI. The
workspace that `operonx project init` makes is an ordinary operonx
project: it calls `operonx init` and then adds its `[project]` block, so
the studio opens it like any other, and its runs land in the project's
trace store.
---

## 7. A task: the rounds inside `task_flow`

One task attempt is one journalled run of this module-level graph. The
code below is a sketch; P3 builds and tests the real one.

```python
@graph
def task_flow(mission_id, task_id):
    PARENT.declare(answers=[], outcome=None, reducers={"answers": operator.add})
    p = prepare(mission_id=mission_id, task_id=task_id)       # worktree, test author (once)
    r = round_(mission_id=mission_id, task_id=task_id, answers=PARENT["answers"])
    ask = InterruptOp(payload=r["question"])                   # parks; you answer on Telegram
    k = keep(response=ask["response"])
    k["answers"] >> PARENT["answers"]
    r["outcome"] >> PARENT["outcome"]
    START >> p >> r >> if_(r["next"] == "ask", ask).if_(r["next"] == "fix", r).else_(END)
    ask >> k >> r                                              # resume → next round, with the answer
```

**One `round_` execution is one round**: an op with `child()` steps, so
the trace reads `round_[n] → implement → checks → review`. Each round is
journalled when it ends, so a crash re-runs only the round that was going.
Its `next` output loops back (`fix`), parks (`ask`) or ends the run:

1. **implement:** the coder in the worktree. The first round starts a
   session; later rounds `--resume` it with only the new information
   (failing check tails, review findings, your answer). The session id is
   stored as soon as Claude's `init` event names it.
2. **commit** whatever changed (`gitops`). An empty diff on a code task is
   one retry with that fact, then a failure.
3. **guard:** if the diff touches protected acceptance tests or paths
   outside the task's `paths`, it is rejected with that as the finding.
   Outside paths are allowed, with a warning, when declared shared, such
   as `pyproject.toml`.
4. **checks:** the baseline commands plus the acceptance tests, and for an operonx target the convention checks of §6.1, run in
   the worktree, give exit codes and output tails.
5. **review:** only when the checks pass. A fresh reviewer session gets
   the diff, the criteria and the check results. Its findings go through
   the mechanical citation check (§2, decision 5).
6. **decide:** a pure function, unit-tested, from the round's facts:

| Facts | Next |
|---|---|
| checks pass, no blocker or major finding | **verified**: the run ends, and the scheduler integrates |
| a check fails with a new failure | fix: the same session resumes with the failure tail |
| the same failure signature 3 rounds in a row | **revise approach** once: a new implementer session gets a summary of what failed; then escalate |
| blocker or major findings | fix: the session resumes with the findings |
| a finding with the same fingerprint again after a fix | arbitration: one third session sees both sides; if still unresolved, escalate |
| the implementer reports `needs_human` | triage (§9.1): answered from evidence, back to fix; or **ask** |
| `max_rounds` (6) or the task budget is reached | escalate with a choice: "keep trying with X / skip / stop", never a silent loop |
| a rate limit on the coder | pause the mission until the reset time (§10) |
| an infra error (the binary is gone, the disk is full) | the task fails with the reason; dependents are blocked |

A round inside a task is never retried blindly. Each repeat carries new
information: a new failure, a new finding or your answer. The decision
table is the termination proof. Every path either ends in *verified*,
*ask* or *failed*, or consumes one of a finite set of counters (rounds,
same-signature repeats, arbitrations, revisions, budget).

---

## 8. The scheduler

### 8.1 States

**Task**

| state | meaning |
|---|---|
| `pending` | dependencies not merged yet |
| `ready` | could start; waiting for a slot or a path lock |
| `running` | its `task_flow` run is going |
| `waiting_human` | its run is parked on a question |
| `verified` | passed; waiting for the merge lock |
| `done` | merged, with regression checks green |
| `failed` | gave up, with a reason and evidence |
| `blocked` | a dependency failed or was cancelled; the reason names it |
| `cancelled` | you cancelled it, or a replan removed it |

**Mission**

| state | meaning |
|---|---|
| `discovering`, `planning`, `awaiting_approval`, `running` | the lifecycle stages (§6) |
| `waiting_human` | nothing can run, at least one question is open |
| `paused` | you paused it, or a rate limit; it can carry a `resume_at` |
| `done` | delivered |
| `failed` | nothing can run, and something failed |
| `cancelled` | you cancelled it |
| `budget_exhausted` | a mission budget ran out; it is asking you |

Every transition is one function in `store.py`. It checks that the
transition is legal (a table), writes the event row, and is unit-tested.
An illegal transition raises.

### 8.2 Running the DAG

- **Waves** are only for display (depth in the DAG). Scheduling is
  dynamic: a task starts as soon as its last dependency is `done`, not
  when its whole wave is done.
- A task starts when it is `ready`, a slot is free (`max_parallel`,
  default 2), no running task's `paths` overlap its own, and the mission
  is `running`.
- **When a dependency fails or is cancelled**, every task downstream of
  it becomes `blocked` with the reason. **When a dependency is waiting on
  you**, the tasks downstream of it stay `pending` ("waiting on T4's
  decision"), and **independent tasks keep running**.
- **Integration** happens in order under one lock:
  1. merge the task branch into `mission/<m>`;
  2. on a conflict, the task gets a new round "rebase onto the
     integration tip", with the conflict as input, then review and checks
     again;
  3. after the merge, the full checks run; a new failure against the
     baseline is a regression, and the task is re-opened with it.

  There are at most 2 integration re-opens per task.

### 8.3 Budgets and stopping

| Budget | Default | When it runs out |
|---|---|---|
| `max_usd` (from Claude's `total_cost_usd`; notional on Max) | 30 | 80 %: one heads-up message; 100 %: `budget_exhausted` and a question (extend / stop) |
| `max_hours` (wall clock while running; waiting for you does not count) | 8 | same |
| per task: `max_rounds`, `max_usd` | 6, 5 | escalate (§7) |
| `max_questions` you get per mission without a digest | 5 | further questions are batched into one digest message |
| `max_replans`, `max_tasks`, plan-review rounds | 2, 25, 2 | ask |

Each coder call also gets `--max-budget-usd` and a wall-clock timeout, so
a single runaway session is cut by Claude Code itself.

### 8.4 Why it ends

A mission ends because each loop draws on a finite counter: task rounds,
plan reviews, replans, integration re-opens, budgets. When the counters
run out, a question goes to you rather than another attempt. The
scheduler itself only acts on events (a run ended, a command arrived, a
timer fired). It never spins.


### 8.5 The board: cards, columns, pull and WIP limits

A card's state (§8.1) maps onto a board column:

| Column | Card states | WIP limit (default) |
|---|---|---|
| Backlog | rows in scope with no card yet (W2), or cards whose dependencies are open | — |
| Ready | `ready` | `ready_depth` = 2 × slots (W2 tops it up) |
| In progress | `running` (the implement step) | `max_parallel` (2) |
| Review | `running`, in its review step | 2 |
| QA | `running`, in its checks / held-out step; `verified` waiting for the merge lock | 2 |
| Waiting on you | `waiting_human` | none: a question never waits for a slot |
| Done | `done` (W2: its rows `implemented_verified`) | — |
| Blocked / Failed | `blocked`, `failed` | — |

- **Pull (W2):**
  - a free implement slot takes the highest-priority Ready card whose
    paths are free;
  - priority comes from the plan (epic order, then risk first, then
    unblocking count);
  - a full Review or QA column stops new implement starts, so finished
    work is verified before more is started: kanban's flow rule, which
    keeps work-in-progress (and merge conflicts) small.
- **Push (W1):** the same columns, with no column limits beyond
  `max_parallel`.
- **The replenisher (W2)** runs when Ready drops below `ready_depth`. It
  is one planning call that details the next cards from the epic plan
  and what the finished cards recorded. It is bounded by `max_cards` and
  by the plan's own stopping rule (no rows left in scope).

---

## 9. You in the loop: Telegram, durable waiting, exact resumption

### 9.1 Asking only when it must

Before any question reaches you, **triage** runs. It is a short,
read-only session with the repo, the brief, the goal and the mission's
decisions so far. It gets the question and must do one of:

- **answer it from evidence**, citing the files or docs. The answer goes
  back to the implementer as a decision, and you never see it;
- **decide it as routine** (reversible, local, low risk). The engine
  records an *assumption*, which is listed in `DELIVERY.md`;
- **escalate.** It writes the message (§9.2). Escalation is allowed only
  for these categories:
  - ambiguous or conflicting requirements;
  - a consequential trade-off: architecture, public API, data migration;
  - credentials or access that are missing;
  - authorization: anything irreversible or outward-facing;
  - repeated failure (§7);
  - budget.

Questions are also bounded: a digest after `max_questions`, and never the
same question twice. The fingerprint of task, category and question
deduplicates them.

### 9.2 The message

```
🧩 leak-watch · C4 Office documents · Q-01 — needs your decision

The goal says "flag office documents", but policy/CONTRIBUTING.md (SRC-02) says
the .docx templates in docs/templates/ are committed on purpose. Flagging all
.docx would block those commits in every repo that follows the policy.

Tried: read both sources, and searched for a rule that ranks them. Neither the
goal nor the policy says which wins.

A) ✅ Allow docs/templates/ only, block .docx elsewhere — recommended:
   keeps the templates workflow, still blocks stray documents.
B) Warn on office files everywhere, never block — no workflow breaks, weaker guard.
C) Block everywhere; templates move out of the repo — strongest, breaks the workflow.

Reply to this message in your own words, or tap a button.
Other cards continue: C2, C3, C5 are moving. C6 waits for this.
[ A (recommended) ] [ B ] [ C ] [ ✏️ Other… ]
```

The message is rendered from the interaction record (project, task,
problem, why it blocks, what was tried, evidence, options, a
recommendation, what is needed, what continues meanwhile). It passes
through a redactor (operonx-agents' `redact` plus secret patterns), and
it is cut to Telegram's 4096 characters, with a "details" link to the
studio when a link exists.

### 9.3 Waiting, durably

1. The `InterruptOp` in `task_flow` parks the run. The journal holds the
   question and the run ends `interrupted`.
2. The scheduler sees the parked run and creates the interaction (id =
   `interrupt_id`, plus its `context_hash`, §9.5). The task becomes
   `waiting_human` and the message is sent. The ids of the sent messages
   are stored in the interaction.
3. Nothing waits in memory: no process, model or timer belongs to this
   task. A daemon restart, a reboot or a deploy changes nothing.
4. Your reply (§9.4) → `submit_answer` → a `resume` command on the queue
   → the scheduler calls `engine.resume(run_id, answers={iid: answer})`.
   The journal replays everything that had ended, and the `InterruptOp`
   returns your answer. The next `round_` resumes the *same* Claude
   session with:
   > The owner decided: "allowlist, but warn".
   > Option A was chosen. Continue the task.
5. The interaction becomes `delivered`. The Telegram message is edited to
   "✅ Answered: A, with your note, at 14:02; T4 resumed".

### 9.4 Matching a reply to a question

- **A button** is a `callback_query`. Its `callback_data` is
  `a:<iid 8 hex>:<option>:<version>` (at most 64 bytes, checked). The
  query's `message.message_id` must be one of the interaction's sent
  messages.
- **A reply to the message** (`reply_to_message.message_id`) maps to that
  interaction. The free text is the answer, kept verbatim. Triage turns
  it into `{option: A|B|C|other, instructions: "..."}` only to label it.
  The implementer always gets your exact words.
- **A plain message that is not a reply.** If exactly one question is
  open in that chat, the message answers it, and the bot confirms which
  one it took. Otherwise the bot lists the open questions and asks you to
  reply to the one you mean. A guess is never taken.
- **Commands** (one handler with validated arguments):
  - `/status`, `/pending`, `/mission <id>`;
  - `/pause`, `/resume`;
  - `/cancel <id>`, which needs a second confirmation tap;
  - `/budget <id> +10`.

### 9.5 Late, duplicate and stale replies; delivery failures

- **Duplicate:** each Telegram `update_id` is processed once (a
  processed-ids table; also the `offset` ack). A second answer to an
  answered question gets "already answered (A, 14:02)", with no change.
- **Stale:** every interaction carries a `context_hash` of the task spec,
  the base commit, the question version and the mission state. A replan,
  a cancel, a pause→resume that changed the context, or the task having
  moved on makes the interaction `superseded`. A late answer then gets
  "this question is no longer current, here is the current one (if any)".
- **Delayed:** nothing expires early. By default a reminder goes out once
  after 24 h. At `expires_at` (72 h by default) one of two things happens:
  - if the question had a *safe default* (the recommended option, marked
    reversible by triage), the engine takes it and says so;
  - otherwise the task stays `waiting_human` and the mission summary
    flags it.
- **Delivery failure:** sends retry with backoff (`retry_call`, the
  `TRANSIENT` classifier). The interaction keeps `delivery: pending |
  sent | failed`. The question still stands, and `operonx project pending`, the
  studio and `operonx project answer` work without Telegram.
- **Restart in the middle of an answer:** the answer is in the store
  before the queue item is acknowledged. A daemon that dies after
  `submit_answer` and before the resume finds the `resume` command on
  start. Resuming is idempotent: a run that is no longer parked is
  refused with 409 and recorded.

### 9.6 Security

- **Allowlist:** Telegram user ids *and* chat ids, from config. Anything
  else is dropped, and logged with the user id only. The bot never
  replies to strangers, so its existence and its missions are not leaked.
- **The token** comes from the environment or `secrets.yaml`. It is never
  logged and is redacted in traces (it is part of every Bot API URL, so
  the HTTP client logs only paths).
- **Telegram cannot bypass policy.** It is one more caller of the same
  `submit_answer` and command functions the CLI uses. An approval that
  the policy does not allow is refused there, not in the Telegram code.
- **Approvals are tied to their context.** An approval of plan v2 cannot
  approve plan v3 (§9.5).
- **Outbound content** passes through the redactor. Repo content is
  untrusted (§11), so the message shows evidence as quoted text, never as
  instructions to you.

---

## 10. Reliability and economics

**Crash matrix.** The daemon is killed with `kill -9` at each point. All
of these are tested in P9.

| Killed while | On restart |
|---|---|
| discovering / planning | the journal resumes the stage; the coder session resumes by its stored id |
| a task's coder session is running | the orphaned `claude` process group (pgid stored) is killed; the round re-runs and resumes the session (`--resume`); the worktree keeps the files |
| checks are running | the round re-runs; checks run again (they are idempotent) |
| waiting for you | nothing; the question stays open |
| between `submit_answer` and the resume | the `resume` command is found in the queue and run |
| merging | merges are atomic per command; a half-written merge (`MERGE_HEAD` present) is aborted and redone under the lock |

**Single daemon.** A lease in `missions.db` stops two daemons from driving
one workspace. A stale lease expires after 30 s.

**Rate limits.** A Claude rate-limit reply carries its reset time. The
mission becomes `paused` with `resume_at`, a timer is set (not a polling
loop), and you get one message.

**Cost tracking.** Each coder call's `total_cost_usd`, tokens and turns
go on its round and are summed per task and per mission. On Max this is
the notional API cost, which is still the right measure for budgets and
comparisons.

**Context economy.**
- The repo brief is written once and handed to every session.
- An implementer's later rounds resume its session with only the new
  information.
- Reviewers get the diff and the criteria, not the repo; they read files
  on demand.
- No session ever gets the whole mission history.

**Traces.** Every run carries `metadata={mission, task, round, role}`.
The studio's Runs tab lists them; a task's run page shows `round_ →
implement / checks / review` as `child()` steps. Coder transcripts
(stream-json) are kept per round on disk for the studio to render.

**Logs.** These go through the operonx `LOGGER` (never `print`). One
`events` row per state change is the audit log: who, what, when, why.

---

## 11. Security boundaries

| Boundary | How |
|---|---|
| Files the implementer can write | `cwd` = its worktree. Claude file tools are confined to it (`--restricted` + `--tools` naming Bash for the implementer only). Paths are guarded per task (§7, step 3). |
| The implementer's shell | **D2:** bubblewrap sandbox (no network, writes only in the worktree), or no boundary on a trusted repo, said plainly |
| Check commands | `docker run --rm --network none -v <worktree>:/work`, an image named in the mission policy (host execution is opt-in for trusted repos) |
| Credentials | the child environment is scrubbed (the studio's `base_env`: no `ANTHROPIC_*`, `CLAUDE*`, no project `.env`); deny rules `Read(**/.env*)`, `Read(~/.claude/**)`, `Read(<project state>/**)` |
| Git | the engine owns branches; the coder's `git push`, `git remote`, `git config`, `reset --hard` on other branches are denied; push or merge to a base branch only by approval |
| Untrusted repo content | file contents and tool output are data: the role prompts say so, the reviewer's and triage's tools are read-only, and nothing a repo file says can change the mission policy (policy lives in `missions.db`, set by you) |
| Telegram | §9.6 |
| Studio | the studio's login is the boundary; its Missions tab calls the `operonx project` CLI under the workspace's interpreter, so the same checks apply |

---

## 12. The studio: a Missions tab with a board

**Why the studio and not a separate UI.** It already has the shell (tabs,
themes, auth, phone layout), run pages for each card attempt, the diff
view, live runs, the answer card and Monitor. A separate app would
rebuild all of that. The engine stays usable without the studio: the
CLI, Telegram and `DELIVERY.md` cover everything.

**How it reads and acts.**
- **Reads** go straight to `missions.db`, read-only, and to the target
  repo's BRD folder. The schema is documented and versioned
  (`schema_version`), the way the studio already reads run stores.
- **Actions** (answer, approve, pause, resume, cancel, reprioritize a
  card) run `operonx project api <json>` under the workspace's interpreter, so
  validation lives in one place, the same as the playground bridge.
- Updates arrive by cursor, like the assistant and the playground.

**Three views of one mission**, because they answer three different
questions:
- **Board** (default): what is moving, and what is stuck?
- **Checklist**: which requirements are met, with what evidence?
- **Graph**: what depends on what, and which waves?

```
Missions ▸ leak-watch            W2 · running · rows 9/14 verified · $4.10 / $30 · 1h12m
[ Board ] [ Checklist ] [ Graph ]
┌ Waiting on you (1) ─────────────────────────────────────────────────────────────────┐
│ Q-01 conflict · C4 Office documents — allowlist templates or block everywhere? [A][B][C][✏️]│
└──────────────────────────────────────────────────────────────────────────────────────┘
 Backlog (2)   Ready (2/4)    In progress (2/2)  Review (1/2)   QA (0/2)   Done (3)
 ┌─────────┐   ┌──────────┐   ┌──────────────┐   ┌──────────┐              ┌─────────┐
 │C6 inst. │   │C5 size   │   │C2 archives   │   │C3 secrets│              │C1 CLI ✓ │
 │ needs C4│   │ R-07     │   │ round 2 · ✗1 │   │ 2 findings│             │ R-01,02 │
 └─────────┘   └──────────┘   └──────────────┘   └──────────┘              └─────────┘
 Card C2 · archives · rows R-03, R-04 · round 2 of 6
   criteria  3/4 (EARS: "When a staged file is a .tar.gz, the hook shall refuse…" ✗)
   rounds    1: implement → checks ✗ (1 failing) → fix   2: implement → checks …
   held-out  —        QA  pytest 41 passed, 1 failed     diff +184 −3 (4 files)
   docs      feature file ✓  matrix row ✓  changelog —
   [open run trace] [transcript] [diff] [rows in checklist]
```

**Checklist** is the matrix: one line per row, its status, its card, and
the cited tests with pass or fail from the last run. Filters are
not-verified, blocked by a `Q-nn`, or changed by a new source. **Graph**
is the card DAG in waves (§8.2), drawn with the studio's plain-wire
conventions; the task graph is drawn fresh and changes no existing canvas
edges.

It also has: the plan and its review notes (v1 → v2), `Q-nn` with
resolutions and ADRs, retries with their reasons, events, and budget
charts (Monitor's chart conventions, dataviz). It is built on a studio
branch, with screenshots on desktop and phone after each change (your UI
rule).

---

## 13. The proof of concept, and the bake-off inputs

**S: `leak-watch`, a real, small, useful operonx project.** The engine
creates it with `operonx init leak-watch --template hello`, the template
for "a pure-compute feature, run as a job and served over HTTP", which
is exactly its shape. It has:
- a Job `scan_repos` with one item per git repo under a root. For each
  repo it classifies the tracked files: archives (`.zip`, `.tar.*`,
  `.7z`), office documents, `.env*`, private keys and token patterns, and
  files over a size limit. Its `reduce=` writes one report, and it runs
  with `schedule=schedule(at="07:00")`;
- a Service `POST /check` that classifies a list of paths;
- `leak-watch install`, which writes a pre-commit hook that runs the same
  graph locally, so a commit is stopped before it leaks;
- pytest on real temporary git repos, and the operonx conventions checked
  (§6.1).

It answers a real incident: your zips leaked to a public repo through
`git add -A`.

There are **two sources**, as a BRD often has:
- SRC-01, the goal paragraph;
- SRC-02, the team's `CONTRIBUTING.md` (seeded under
  `/home/thanglq/project-poc/policy/`), which says that `.docx` templates
  in `docs/templates/` are committed on purpose.

That makes "flag office documents" a **genuine conflict between two
sources** (`Q-01`): no evidence settles it, so it is your call. The target
repo, `/home/thanglq/project-poc/leak-watch`, starts empty with its own
`git init`, outside every existing repo.

Whether the engine actually escalates it is itself a test of §9.1. If
triage "decides" it silently, the run shows that the policy is wrong; the
rule is fixed and the run repeated. I will not soften the scenario to
make the run pass. **Held-out tests** for S are written by me from the
goal before the first run, committed outside the target repo, and hashed
in §17.

**M: a replay of a finished OneWealth phase** (§5.6), on a local copy.
- The repo is checked out before the phase; the BRD folder is as it was.
- The scope is that phase's rows; the held-out tests are the tests that
  phase really added.
- It is a brownfield repo with a real BRD (Vietnamese source quotes, a
  `.docx` that must stay out of git), so it also exercises §4 on real
  input.

**E0: an extraction check on OneWealth's original BRD** (the `.docx`,
read locally, never copied into git). `brd_flow` runs on it alone, and
its output is compared with the reviewed extraction in
`mr-finance/docs/brd/requirements/`:
- recall of the 61 numbered requirements and 7 KPIs (they have codes, so
  this is exact);
- recall of the 97 rules (matched by a judge on meaning and section,
  with 20 matches checked by hand);
- quotes that are not in the source (must be 0);
- false requirements (inventions).

It costs one requirements session, and it measures the stage that no
other test does.

**What the runs demonstrate, all together:**
- input in any form → a living BRD → checklist → board, and goal → spec
  → DAG;
- parallel cards in worktrees, real code, real pytest;
- review → fix rounds from real findings, and a real check failure → fix;
- held-out tests catching what visible tests missed;
- the Telegram question (`Q-01`) while other cards continue;
- a daemon restart while waiting;
- the answer → the exact resume → the card done;
- integration and regression;
- the matrix verified by the checker;
- `DELIVERY.md`;
- the board, checklist and graph showing all of it.

**A further real mission** (P10) runs with the winning mode on a live
backlog: OneWealth rows still `planned`, or an Operon finding, whichever
you choose then. It is delivered as a branch for your review and never
merged by the engine.

---

## 14. Phases, gates and verification

Each phase is one branch and one PR (your rule: one branch per phase,
batched commits), merged when its gate passes. The commands and results
of each gate go into §17.

**P1. Store, model, cards, scheduler (no LLM)**
- **Builds:** the package skeleton in `packages/operonx-project`, the CI
  matrix entries, the mission store with its transition table, cards
  with dependencies, the board columns and WIP limits, the knobs of
  §5.5, and the scheduler driven by fake card runners.
- **Gate:**
  - unit tests of every transition, waves, the ready set, blocked
    propagation, path locks, push vs pull, the column limits and budgets;
  - **a property test**: on random DAGs with random durations and
    failures, under both policies, a card never starts before its
    dependencies are done, no limit is exceeded, overlapping paths never
    run together, every card ends in a final state or blocked-with-reason,
    and the scheduler's step count is bounded.

**P2. Coder, git, checks**
- **Builds:**
  - `ClaudeCodeCoder`, ported from the studio: stream-json, the session
    id at `init`, `--json-schema` output, cost, timeouts, cancel (process
    group), rate-limit detection;
  - `gitops`: worktrees, commits, merge under a lock, conflicts, the
    private-source guard;
  - `checks`: host and docker runs, the baseline;
  - in core, `operonx lint` (moved from the studio) with the new rules
    C7–C12 (§6.1), as its own PR;
  - in the studio, right after it, one PR that splits its
    `operonx_project` module (D1):
    - core's manifest and `operonx init` replace the studio's own;
    - lint and build check come from core;
    - extract and the edits are renamed `operonx_studio_toolkit`.

    It lands before any project with operonx-project installed is opened
    in the studio (P5);
  - `apt install bubblewrap` and Claude Code's bash sandbox (D2).
- **Gate:**
  - the adapter's tests against a scripted fake `claude` executable (a
    test double, labelled);
  - git tests on real temporary repos;
  - `operonx lint`: each new rule has a failing case and a passing case;
    it runs clean on the `operonx init` templates and on OneWealth; the
    studio's whole suite passes after the split, and its "New project"
    creates the same tree as `operonx init`;
  - the sandbox refuses a write outside the worktree and a network call,
    and both refusals show up in the round's record;
  - **one paid smoke** with the real Claude Code: a session that creates
    a file, is resumed, and returns `--json-schema` output. It checks the
    event shapes we parse, about $0.10 notional.

**P3. `task_flow` and the decision table**
- **Builds:** the graph (§7), `round_`, `decide()`, guards, the test
  author, the examiner hook (V2), integration.
- **Gate:**
  - with a scripted coder: the card passes; it fails then fixes; review
    findings lead to a fix; a repeated finding leads to arbitration and
    escalation; the same failure 3 times leads to a revise and then an
    escalation; a held-out failure returns only names and messages;
  - `max_rounds` is honoured;
  - the guard rejects an edited acceptance test and a private source;
  - a park → resume through the journal with a new engine instance;
  - **`kill -9` mid-round** → resume, with no completed round re-run (by
    counting calls);
  - a merge conflict leads to a rebase round.

**P4. Requirements: the living BRD (§4), and E0**
- **Builds:**
  - readers for `.docx`, PDF, images, zip and text;
  - the source register (hashes, private sources);
  - `brd_flow`, with the quote, coverage and ID checks and the
    miss-hunter;
  - the `Q-nn` writer;
  - the generalized template (`operonx project brd init`) and checker
    (`operonx project brd check`);
  - diffing a new source version.
- **Gate:**
  - unit tests of each reader on real files (a `.docx` with tables and
    figures, a scanned PDF page, a zip with an image);
  - the checks catch a planted fake quote and a skipped section;
  - the checker on OneWealth's real folder agrees with
    `check_brd_docs.py`;
  - **E0 (paid, one session):** recall of numbered requirements and
    KPIs ≥ 95 %, of rules ≥ 80 %, with 0 quotes not in the source. These
    are first targets; the measured numbers go into §17 whatever they
    are.

**P5. Lifecycle, both modes, daemon, CLI**
- **Builds:**
  - discover (with the baseline), requirements, plan in both modes
    (whole DAG; epics plus the replenisher);
  - plan review with the mechanical checks, approval;
  - the examiner, final verification, `DELIVERY.md`;
  - the daemon (lease, recovery pass, command queue) and the CLI.
- **Also builds:** the greenfield path (§6.1): the `operonx init` card,
  the template choice, and the operonx QA commands.
- **Gate:**
  - the whole lifecycle in both modes with the scripted coder;
  - a greenfield mission creates its project with `operonx init`, and
    its QA runs pytest, the build check, `operonx lint` and
    `operonx guide --check`;
  - the plan-review cap;
  - the replenisher keeps Ready at its depth and stops when no rows are
    left;
  - replanning keeps done cards;
  - the recovery pass covers each crash-matrix row that P3 does not;
  - `operonx project status` and `operonx project show` are accurate against the store.

**P6. Interactions and messaging (Telegram still mocked)**
- **Builds:** the interaction lifecycle as `Q-nn`, `submit_answer`,
  triage, rendering, the redactor, expiry and reminders, the `Messenger`
  protocol, `TelegramMessenger`, `FakeBotAPI` (a real HTTP server on
  localhost), and commands.
- **Gate (against `FakeBotAPI`, labelled as a test double):**
  - a button answer, a reply-to answer, an unthreaded answer with exactly
    one question open and with two open;
  - duplicate `update_id`; a second answer;
  - a stale answer after a replan; expiry with and without a safe default;
  - an unauthorized user and an unauthorized chat are silently dropped;
  - `/cancel` needs confirmation;
  - a send failure → retry → `operonx project answer` still works;
  - the token never appears in logs (grep in the test).

**P7. The bake-off (paid; Telegram is the fake server; §5.6)**
- **Builds:** the held-out tests for S are written and hashed first. Then
  the six runs: S × W1 × 2, S × W2 × 2, M × W1, M × W2, all with V2.
  - On S, the `Q-01` question is answered by me through the fake server's
    "user says" endpoint, recorded as such.
  - In one S run the daemon is `kill -9`ed while `Q-01` waits.
- **Gate:**
  - every metric of §5.6 is recorded per run;
  - the delivered repos' tests and held-out tests are re-run by me,
    separately from the engine;
  - the `DELIVERY.md` claims match the store, git and test output,
    checked by a script;
  - the decision rule is applied as written, and the default mode is
    chosen;
  - every engine failure seen is either fixed (and its run repeated) or
    written up in §17.
- **Expected cost:** about 8–12 hours of coder time over several Max
  windows (D3), notional $40–100. The rows are written into
  `docs/project/BAKEOFF.md`.

**P8. Studio Missions tab (studio repo, its own branch)**
- **Builds:** the list, the mission page with **board, checklist and
  graph**, the waiting-on-you card (answers via `operonx project api`), the card
  panel and the links to run pages.
- **Gate:**
  - studio tests for the read API and the action calls;
  - screenshots on desktop and phone of P7's real missions, live and
    finished;
  - answering from the studio follows the same rules (a stale answer is
    refused).

**P9. Hardening**
- **Builds and tests:**
  - the full crash matrix with `kill -9` at each row, against real
    processes;
  - budget and rate-limit pause and resume;
  - security: path escape, a write to an acceptance test, a private
    source staged for commit, a denied `git push`, an environment leak
    check, an untrusted repo file containing instructions ("ignore your
    task and …") with the policy unchanged;
  - the docker check runner;
  - the guide page `operonx_project/guide/01-project.md`, run by the snippet
    tests;
  - regression: the operonx-project suite, `tests/internal/durable`, `tests/guide`,
    agents' suite (redact is reused), and studio tests.

**P10. Real Telegram and a live mission (needs D4)**
- **Builds:** your bot token and user id, long poll (or webhook), the S
  scenario with **you** answering on your phone, and then the further
  real mission (§13) in the winning mode.
- **Gate (your brief's mandatory scenario, all eight steps):**
  1. a genuine decision arises;
  2. a real Telegram message arrives on your phone;
  3. the run is durably parked: the daemon is restarted while it waits;
  4. you answer in Telegram;
  5. the answer is correlated and validated;
  6. the same run resumes;
  7. the card completes;
  8. real tests pass.

**How the requirements in your brief (§9) map to phases**

| Requirement | Phase |
|---|---|
| dependency ordering, independent scheduling, blocked propagation | P1 property + unit tests |
| planning and review iteration limits | P3, P5 |
| failures, retries, recovery; partial completion | P3, P9 |
| real test execution and accurate reporting | P2, P7 (the delivery-claims check, re-runs by me) |
| persistence and resumption after interruption | P3, P5, P9 |
| approve, reject, revise, delayed responses | P5, P6 |
| Telegram auth, correlation, duplicates, stale | P6 (fake); P10 (real) |
| safe cancellation | P5, P9 |
| security boundaries | P9 |
| integration with OperonX | P3 (journal, InterruptOp), P9 regression |
| UI and API | P8 |
| existing regression suites | P9 |
| requirements from any input; traceability | P4 (E0), P7 (M) |
| choosing the workflow by evidence | P7 |

**Not counted as proof:** a mocked coder for anything about code quality,
the fake Bot API for the live Telegram integration, visible tests alone
for quality (held-out tests are the measure), and the compilation,
typing or happy path alone.

---

## 15. Not building, or later

- **A general workflow runtime, or a "graph of missions".** The DAG is
  scheduler data, as Jobs' steps are.
- **A coder backend on operonx-agents** (an OpenRouter model with file
  and shell tools). This is later: it would make review vendor-independent
  and run where there is no Claude login. The `Coder` protocol leaves room
  for it.
- **More workflow candidates** (an agent per role à la BMAD, Spec Kit's
  constitution file). §5.3 says why not now; P7's numbers can re-open it.
- **Missions across several repos**, running the same task in several
  worktrees, and choosing the best.
- **Postgres** for the mission store. SQLite holds one workspace on one
  machine. The store sits behind a small interface, as the journal does.
- **Slack or e-mail messengers.** The `Messenger` protocol leaves room
  for them.
- **Core changes.** None are expected. Any gap P1–P9 finds goes upstream
  as its own PR, with a failing test first (for example, loop ops'
  outputs not reaching the run's outputs, if it turns out to matter).

## 16. Risks

| Risk | What I do about it |
|---|---|
| Claude Code's stream-json or `--json-schema` shapes change between versions | they are parsed in one module; the P2 smoke runs per version; the version is recorded on every round |
| The reviewer approves bad code | the commands decide, not the reviewer alone; acceptance tests are written first by another session; the final whole-diff review |
| Triage escalates too much, or too little | P7 measures both: every question asked and every assumption taken is listed in `DELIVERY.md`; the rules are tuned on that evidence |
| The Max quota runs out mid-mission | the rate-limit pause (§10); D3 |
| A shared host (staging callbot) is slowed | `max_parallel` = 2; checks in docker with CPU limits |
| Merge conflicts between parallel tasks | path locks, then the rebase round, then at most 2 re-opens, then you |
| The bake-off's N is tiny (6 runs) | a pre-registered rule; the result is labelled directional; both modes kept unless one wins clearly |
| The replay (input M) leaks the answer: the agent finds the later code | a fresh clone truncated at the phase's base commit (no later objects, no remote), and the BRD folder at that commit |
| Extraction is good on OneWealth only | E0 is one data point; P4's reader tests use other files; the first BRD of a new kind is reviewed by you once |

## 17. Progress log

(empty: filled in phase by phase with commands run and their results)

## Sources

- Anthropic, *Effective harnesses for long-running agents*:
  https://anthropic.com/engineering/effective-harnesses-for-long-running-agents
- Telegram Bot API (getUpdates, webhooks, callback queries):
  https://core.telegram.org/bots/api
- Context on the planner/implementer/reviewer pattern (secondary):
  https://www.contextstudios.ai/blog/anthropics-2026-agentic-coding-report-orchestration-era.md
