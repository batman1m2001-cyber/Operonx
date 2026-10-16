# operonx-project: an engine that builds software projects on its own

**Status:** 2026-10-17. Plan for review. Nothing is implemented yet. Every
"today" below was read in the code or measured on this machine.

**In one paragraph.** You give the engine a goal and a git repository. It
studies the repo, writes a plan as a graph of tasks with acceptance
criteria, and runs the tasks. Independent tasks run in parallel, each in
its own git worktree. A coding agent (headless Claude Code) implements
each task. A *different* session reviews the diff, and the repo's real
tests decide whether it passes. When it hits a decision it cannot make
safely, it messages you on Telegram. The task's run **parks** in the
journal (nothing waits in memory, no tokens burn), the other tasks keep
going, and your reply resumes that exact run in any process, even after a
restart. It ends with a branch, a PR description and the evidence behind
every claim.

**One example.** Goal: *"Build `commit-guard`, a pre-commit hook that stops
commits that would leak archives, office documents, `.env` files or keys
into a public repo."* The engine plans 6 tasks in 3 waves. Task T4
("office documents") finds that the repo ships `.docx` templates in
`docs/templates/`, which a blanket block would break. It messages you:
*"Block all office files, or allow `docs/templates/`? I recommend the
allowlist."* T4 parks while T2, T3 and T5 carry on. You reply "allowlist,
but warn". T4 resumes in the same Claude session with your answer and
writes the code. A reviewer session that never saw T4's reasoning checks
the diff. pytest runs on real temporary git repos, and T4 merges.

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
  A second probe (`probe2.py`) ran **the exact §5 shape**: `round_` loops
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
| Retries, timeouts | `Retry`, `Timeout`, `retry_call` | the engine's own rules per round (§5) |
| Durable inbound commands | `SqliteQueue` | — |
| Traces and inspection | run stores, studio run pages | `metadata={mission, task, round}` |
| Coding agent | studio's Claude Code plumbing (ported) | a `Coder` protocol, a structured-output schema per role |
| **Dynamic task DAG** | — | **a scheduler above the engine (new)** |
| **Mission state** (tasks, attempts, findings, interactions) | SQLite (as journal, queue and run store already use) | **a mission store (new)** |
| **Telegram** | `retry_call`, the redactor | **a messenger + Telegram adapter (new)** |
| **Dashboard** | studio tab conventions, run pages, diff view | **a Missions tab in the studio (new)** |

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
   in here. A backend on operonx-agents is a later phase (§13).
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
   worktree on `forge/<mission>/<task>`, branched from the integration
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
   checks the actor, the status (`pending`) and the context (§7.5), then
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

### Open: yours to decide

- **D1. The name.** `operonx-project` would import as `operonx_project`,
  which the studio already ships into project environments: same name,
  different code. Options:
  - **(a) `operonx-forge` (recommended).** It imports as `operonx_forge`,
    the CLI is `forge`, and nothing breaks.
  - (b) Keep `operonx-project` and rename the studio's module (to
    `operonx_studio_project`). That touches the studio's CLI entry points,
    `install.sh`, and every project environment it seeded.

  This plan uses `operonx-forge` / `forge` from here on.
- **D2. A real sandbox for the implementer's shell.** Claude Code's bash
  sandbox needs `bubblewrap`, which is not installed. Without it, the
  implementer's Bash runs as this user on this host, which is the same
  box as the staging callbot. Options:
  - **(a) Install `bubblewrap` (recommended).** It is a system package on
    a shared server, so it is your call. The implementer's Bash is then
    confined to its worktree with no network.
  - (b) Proceed without it. Mitigations: a worktree as the working
    directory, a scrubbed environment, deny rules, and check commands run
    in `docker --network none`. The docs then say plainly that this is not
    a boundary, so missions only run on repos you trust.

  Check commands run in Docker either way.
- **D3. Quota.** Missions run on this machine's Claude Max login, the
  same quota as your own interactive Claude Code sessions. A busy mission
  can use up the 5-hour window you also work in. Options:
  - **(a) Max login with `max_parallel = 2` (recommended for the PoC).**
    The engine pauses on a rate-limit reply and resumes at the reset time.
  - (b) An `ANTHROPIC_API_KEY` with a hard spend cap, kept separate from
    your usage.
- **D4. Telegram, at the end** (P9):
  - a bot token (BotFather) and your Telegram user id;
  - long poll (recommended) or webhook through your ngrok domain.

  Nothing before P9 needs them.

---

## 3. Architecture

```
            you ── Telegram ──┐          studio Missions tab ──┐        forge CLI ──┐
                              ▼                                ▼                    ▼
                    ┌───────────────────── commands (SqliteQueue) ─────────────────────┐
                    │  submit_answer · pause · resume · cancel · approve · revise      │
                    └──────────────────────────────┬───────────────────────────────────┘
                                                   ▼
   ┌───────────────────────────── forge daemon (one per workspace) ─────────────────────────────┐
   │  MissionRunner: lifecycle (§4) · Scheduler: DAG, waves, locks, budgets (§6)               │
   │  Messenger: outbound questions, inbound updates (§7) · Recovery on start (§8)             │
   └───────┬──────────────────────────────┬──────────────────────────────────┬──────────────────┘
           │ one journalled Operon run     │ git worktree per task            │ reads / writes
           ▼ per stage / task attempt      ▼                                  ▼
   discover_flow · plan_flow ·      <repo>/.git  ── forge/<m>/<task>    forge.db (mission store)
   task_flow · integrate_flow ·        integration: forge/<m>           journal.db (forge's own)
   final_flow                                                           .operonx/runs (traces)
           │ each round's stages call
           ▼
   Coder (ClaudeCodeCoder: headless `claude -p`, stream-json, --json-schema, --resume)
   Checks (subprocess or docker: the repo's own test / lint / type commands)
```

**The workspace** is a folder made by `forge init`. It holds an
`operonx.toml`, so the studio opens it like any operonx project, and it
has `.operonx/forge/{forge.db, journal.db, missions/<id>/…}`. The **target
repo** is anywhere. A mission names it, and its worktrees live under the
mission folder.

**Package layout** (`packages/operonx-forge/operonx_forge/`):

| Module | What |
|---|---|
| `store.py` | the mission store: schema, transitions (one function per transition, checked), events |
| `model.py` | `Mission`, `Task`, `Round`, `Finding`, `CheckResult`, `Interaction` dataclasses and their state enums |
| `dag.py` | validation (acyclic, deps exist, coverage), waves, ready set, blocked propagation, path locks |
| `scheduler.py` | the asyncio loop: start ready tasks under limits, react to run ends and commands |
| `flows/` | the module-level graphs: `discover_flow`, `plan_flow`, `task_flow`, `integrate_flow`, `final_flow` |
| `coder/` | `Coder` protocol, `ClaudeCodeCoder` (ported from the studio), role prompts and JSON schemas |
| `checks.py` | run declared commands (host or docker), with a timeout, exit code and output tail; baseline diff |
| `gitops.py` | worktrees, branches, commits, merges under a lock, conflict detection, diff for review |
| `interact/` | `Interaction` lifecycle, `submit_answer`, escalation triage, message rendering, expiry |
| `messengers/` | `Messenger` protocol, `TelegramMessenger`, `FakeBotAPI` (test server), `ConsoleMessenger` |
| `daemon.py`, `cli.py` | `forge init / new / up / status / pending / answer / pause / resume / cancel / show` |
| `guide/` | the package's tested guide page, like agents and kb |

---

## 4. The lifecycle

The stages run in this order. Only "execute" is a loop over many runs.
Each stage is adaptive: it is skipped or repeated by rules, not
mechanically.

1. **Intake.** The mission is created from goal text, the repo path,
   optional constraints, and its policy (budgets, approvals,
   models). It is stored and nothing runs yet.
2. **Discover** (`discover_flow`, read-only coder). This writes the repo
   brief: stack, layout, conventions (it reads CLAUDE.md, AGENTS.md and
   CONTRIBUTING), and the commands to install, test, lint and type-check.
   **The commands are then run** to record the baseline: which checks pass
   *before* any change, so a test that was already failing never counts as
   a regression and is never blamed on a task. If a check command cannot
   be found or does not run, that is the first escalation candidate.
3. **Clarify.** The planner lists open questions, and triage (§7.1) sorts
   each one:
   - *decidable*: decide it from the evidence and record the assumption;
   - *owner's, and it blocks the whole plan*: ask now;
   - *owner's, and it blocks only part*: plan that part as a task marked
     `needs_decision`, so the rest proceeds.
4. **Plan** (`plan_flow`, `--json-schema`). It produces requirements
   (R1…) and tasks. Each task has a title, intent, acceptance criteria
   (testable statements, each tied to requirements), `deps`, `paths` (the
   globs it may touch), `kind` (code | test | docs | config), `risk` (low |
   high: migrations, deletes, security, public API), and an estimate.
5. **Plan review.** A fresh session (reviewer model) checks for missing
   requirements, untestable criteria, wrong dependencies, oversized tasks,
   and overlapping paths that ought to be dependencies. The engine also
   checks mechanically:
   - the graph is acyclic and every dependency exists;
   - every requirement is covered by at least one criterion;
   - paths are inside the repo;
   - the task count is at most `max_tasks` (default 25).

   There are at most 2 revise rounds. After that, the plan with its
   unresolved review notes goes to you instead of looping.
6. **Approval** (one tap by default, §2 decision 12).
7. **Execute** (§5, §6): tasks run as their dependencies merge.
8. **Final verification** (`final_flow`):
   - the full checks on the integration branch against the baseline;
   - one review of the whole mission diff, looking at integration
     problems that no single task saw;
   - the traceability matrix: requirement → criteria → tasks → commits →
     the tests that passed.

   A gap re-opens the responsible task (bounded, §6.4).
9. **Deliver.** The integration branch, `DELIVERY.md` (what was built,
   evidence, assumptions taken, decisions you made, known limits, cost)
   and a PR description. A push or PR is an approval (§2, decision 11).

**Replanning.** A task that fails for good, or a "revise" from you, can
change the part of the DAG that has not started yet. That is at most 2
replans per mission. Done tasks are never redone. Questions on tasks that
were replaced become `superseded` (§7.5).

---

## 5. A task: the rounds inside `task_flow`

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
4. **checks:** the baseline commands plus the acceptance tests, run in
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
| the implementer reports `needs_human` | triage (§7.1): answered from evidence, back to fix; or **ask** |
| `max_rounds` (6) or the task budget is reached | escalate with a choice: "keep trying with X / skip / stop", never a silent loop |
| a rate limit on the coder | pause the mission until the reset time (§8) |
| an infra error (the binary is gone, the disk is full) | the task fails with the reason; dependents are blocked |

A round inside a task is never retried blindly. Each repeat carries new
information: a new failure, a new finding or your answer. The decision
table is the termination proof. Every path either ends in *verified*,
*ask* or *failed*, or consumes one of a finite set of counters (rounds,
same-signature repeats, arbitrations, revisions, budget).

---

## 6. The scheduler

### 6.1 States

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
| `discovering`, `planning`, `awaiting_approval`, `running` | the lifecycle stages (§4) |
| `waiting_human` | nothing can run, at least one question is open |
| `paused` | you paused it, or a rate limit; it can carry a `resume_at` |
| `done` | delivered |
| `failed` | nothing can run, and something failed |
| `cancelled` | you cancelled it |
| `budget_exhausted` | a mission budget ran out; it is asking you |

Every transition is one function in `store.py`. It checks that the
transition is legal (a table), writes the event row, and is unit-tested.
An illegal transition raises.

### 6.2 Running the DAG

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
  1. merge the task branch into `forge/<m>`;
  2. on a conflict, the task gets a new round "rebase onto the
     integration tip", with the conflict as input, then review and checks
     again;
  3. after the merge, the full checks run; a new failure against the
     baseline is a regression, and the task is re-opened with it.

  There are at most 2 integration re-opens per task.

### 6.3 Budgets and stopping

| Budget | Default | When it runs out |
|---|---|---|
| `max_usd` (from Claude's `total_cost_usd`; notional on Max) | 30 | 80 %: one heads-up message; 100 %: `budget_exhausted` and a question (extend / stop) |
| `max_hours` (wall clock while running; waiting for you does not count) | 8 | same |
| per task: `max_rounds`, `max_usd` | 6, 5 | escalate (§5) |
| `max_questions` you get per mission without a digest | 5 | further questions are batched into one digest message |
| `max_replans`, `max_tasks`, plan-review rounds | 2, 25, 2 | ask |

Each coder call also gets `--max-budget-usd` and a wall-clock timeout, so
a single runaway session is cut by Claude Code itself.

### 6.4 Why it ends

A mission ends because each loop draws on a finite counter: task rounds,
plan reviews, replans, integration re-opens, budgets. When the counters
run out, a question goes to you rather than another attempt. The
scheduler itself only acts on events (a run ended, a command arrived, a
timer fired). It never spins.

---

## 7. You in the loop: Telegram, durable waiting, exact resumption

### 7.1 Asking only when it must

Before any question reaches you, **triage** runs. It is a short,
read-only session with the repo, the brief, the goal and the mission's
decisions so far. It gets the question and must do one of:

- **answer it from evidence**, citing the files or docs. The answer goes
  back to the implementer as a decision, and you never see it;
- **decide it as routine** (reversible, local, low risk). The engine
  records an *assumption*, which is listed in `DELIVERY.md`;
- **escalate.** It writes the message (§7.2). Escalation is allowed only
  for these categories:
  - ambiguous or conflicting requirements;
  - a consequential trade-off: architecture, public API, data migration;
  - credentials or access that are missing;
  - authorization: anything irreversible or outward-facing;
  - repeated failure (§5);
  - budget.

Questions are also bounded: a digest after `max_questions`, and never the
same question twice. The fingerprint of task, category and question
deduplicates them.

### 7.2 The message

```
🧩 commit-guard · T4 Office documents — needs your decision

The goal says "block office documents", but the repo's own templates live in
docs/templates/*.docx (3 files, committed weekly per CONTRIBUTING.md). Blocking
all .docx would block those commits too.

Tried: read CONTRIBUTING.md and the git log of docs/templates (12 commits since July).
Nothing in the goal or the repo says which wins.

A) ✅ Allow docs/templates/ only, block .docx elsewhere — recommended:
   keeps the templates workflow, still blocks stray documents.
B) Warn on office files everywhere, never block — no workflow breaks, weaker guard.
C) Block everywhere; templates move out of the repo — strongest, breaks the workflow.

Reply to this message in your own words, or tap a button.
Other tasks continue: T2, T3, T5 are running. T6 waits for this.
[ A (recommended) ] [ B ] [ C ] [ ✏️ Other… ]
```

The message is rendered from the interaction record (project, task,
problem, why it blocks, what was tried, evidence, options, a
recommendation, what is needed, what continues meanwhile). It passes
through a redactor (operonx-agents' `redact` plus secret patterns), and
it is cut to Telegram's 4096 characters, with a "details" link to the
studio when a link exists.

### 7.3 Waiting, durably

1. The `InterruptOp` in `task_flow` parks the run. The journal holds the
   question and the run ends `interrupted`.
2. The scheduler sees the parked run and creates the interaction (id =
   `interrupt_id`, plus its `context_hash`, §7.5). The task becomes
   `waiting_human` and the message is sent. The ids of the sent messages
   are stored in the interaction.
3. Nothing waits in memory: no process, model or timer belongs to this
   task. A daemon restart, a reboot or a deploy changes nothing.
4. Your reply (§7.4) → `submit_answer` → a `resume` command on the queue
   → the scheduler calls `engine.resume(run_id, answers={iid: answer})`.
   The journal replays everything that had ended, and the `InterruptOp`
   returns your answer. The next `round_` resumes the *same* Claude
   session with:
   > The owner decided: "allowlist, but warn".
   > Option A was chosen. Continue the task.
5. The interaction becomes `delivered`. The Telegram message is edited to
   "✅ Answered: A, with your note, at 14:02; T4 resumed".

### 7.4 Matching a reply to a question

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

### 7.5 Late, duplicate and stale replies; delivery failures

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
  sent | failed`. The question still stands, and `forge pending`, the
  studio and `forge answer` work without Telegram.
- **Restart in the middle of an answer:** the answer is in the store
  before the queue item is acknowledged. A daemon that dies after
  `submit_answer` and before the resume finds the `resume` command on
  start. Resuming is idempotent: a run that is no longer parked is
  refused with 409 and recorded.

### 7.6 Security

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
  approve plan v3 (§7.5).
- **Outbound content** passes through the redactor. Repo content is
  untrusted (§9), so the message shows evidence as quoted text, never as
  instructions to you.

---

## 8. Reliability and economics

**Crash matrix.** The daemon is killed with `kill -9` at each point. All
of these are tested in P8.

| Killed while | On restart |
|---|---|
| discovering / planning | the journal resumes the stage; the coder session resumes by its stored id |
| a task's coder session is running | the orphaned `claude` process group (pgid stored) is killed; the round re-runs and resumes the session (`--resume`); the worktree keeps the files |
| checks are running | the round re-runs; checks run again (they are idempotent) |
| waiting for you | nothing; the question stays open |
| between `submit_answer` and the resume | the `resume` command is found in the queue and run |
| merging | merges are atomic per command; a half-written merge (`MERGE_HEAD` present) is aborted and redone under the lock |

**Single daemon.** A lease in `forge.db` stops two daemons from driving
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

## 9. Security boundaries

| Boundary | How |
|---|---|
| Files the implementer can write | `cwd` = its worktree. Claude file tools are confined to it (`--restricted` + `--tools` naming Bash for the implementer only). Paths are guarded per task (§5, step 3). |
| The implementer's shell | **D2:** bubblewrap sandbox (no network, writes only in the worktree), or no boundary on a trusted repo, said plainly |
| Check commands | `docker run --rm --network none -v <worktree>:/work`, an image named in the mission policy (host execution is opt-in for trusted repos) |
| Credentials | the child environment is scrubbed (the studio's `base_env`: no `ANTHROPIC_*`, `CLAUDE*`, no project `.env`); deny rules `Read(**/.env*)`, `Read(~/.claude/**)`, `Read(<forge state>/**)` |
| Git | the engine owns branches; the coder's `git push`, `git remote`, `git config`, `reset --hard` on other branches are denied; push or merge to a base branch only by approval |
| Untrusted repo content | file contents and tool output are data: the role prompts say so, the reviewer's and triage's tools are read-only, and nothing a repo file says can change the mission policy (policy lives in `forge.db`, set by you) |
| Telegram | §7.6 |
| Studio | the studio's login is the boundary; its Missions tab calls the `forge` CLI under the workspace's interpreter, so the same checks apply |

---

## 10. The studio: a Missions tab

**Why the studio and not a separate UI.** It already has the shell (tabs,
themes, auth, phone layout), run pages for each task attempt, the diff
view, live runs, the answer card and Monitor. A separate app would
rebuild all of that. The engine stays usable without the studio: the
CLI, Telegram and `DELIVERY.md` cover everything.

**How it reads and acts.**
- **Reads** go straight to `forge.db`, read-only. Its schema is
  documented and versioned (`schema_version`), the way the studio already
  reads run stores.
- **Actions** (answer, approve, pause, resume, cancel) run `forge api
  <json>` under the workspace's interpreter, so validation lives in one
  place, the same as the playground bridge.
- Updates arrive by cursor, like the assistant and the playground. The
  tunnel cuts long responses, so no single long request is used.

**Screens.** Wireframe:

```
Missions ▸ commit-guard                         running · 3/6 done · $4.10 / $30 · 1h12m
┌ Decisions waiting (1) ───────────────────────────────────────────────────────────┐
│ T4 Office documents — allowlist templates or block everywhere?   [A] [B] [C] [✏️] │
└───────────────────────────────────────────────────────────────────────────────────┘
 Wave 1            Wave 2                 Wave 3
 ┌──────────┐     ┌───────────────┐     ┌──────────────┐
 │T1 CLI ✓  │──┬─▶│T2 archives ●  │──┬─▶│T6 installer ○│
 └──────────┘  │  └───────────────┘  │  └──────────────┘
               ├─▶│T3 secrets ●   │──┤
               │  └───────────────┘  │
               └─▶│T4 office ?    │──┘     ✓ done ● running ? waiting on you
                  └───────────────┘        ✗ failed ⊘ blocked ○ pending
 Task T2 · archives      round 2 of 6
   criteria  3/4 met (R2.1 ✓ R2.2 ✓ R2.3 ✓ R2.4 ✗ test_tar_gz_detected)
   rounds    1: implement → checks ✗ (1 failing) → fix   2: implement → checks …
   review    —       QA  pytest 41 passed, 1 failed   diff +184 −3 (4 files)
   [open run trace] [transcript] [diff]
```

It also has: the plan and its review notes (v1 → v2), the traceability
matrix, retries with their reasons, events, and budget charts (reusing
Monitor's chart conventions, dataviz). It is built on a studio branch,
with screenshots on desktop and phone after each change (your UI rule).
The DAG uses the studio's plain-wire conventions. The task graph is drawn
fresh; it does not change any existing canvas edges.

---

## 11. The proof of concept

**The mission:** `commit-guard`, a real, small, useful tool. A
pre-commit hook and CLI (`pip install`, `commit-guard install`) that stops
a commit from leaking into a public repo:
- archives (`.zip`, `.tar.*`, `.7z`), office documents, `.env*`;
- private keys and token patterns;
- files over a size limit.

It is tested with pytest on real temporary git repos. It answers a real
incident: your zips leaked to a public repo through `git add -A`.

**The target repo** is `/home/thanglq/forge-poc/commit-guard`, a new
folder with its own `git init`, outside every existing repo. It is seeded
with a README stub, a `CONTRIBUTING.md` that says the team commits
`docs/templates/*.docx`, and three such templates. That seed makes the
office-documents rule a **genuine conflict between the goal and the
repo**: the goal says block, and the repo's own workflow needs those
files. No evidence settles it, so it is your call. That is the T4 example
at the top.

Whether the engine actually escalates it is itself a test of §7.1. If
triage "decides" it silently, the run shows that the policy is wrong, and
the escalation rule (conflicting instructions → ask) is fixed and re-run.
I will not soften the scenario to make the run pass.

**What it demonstrates:**
- goal → plan with requirements, criteria and a DAG of about 6 tasks in
  3 waves;
- 2 tasks running in parallel in worktrees;
- real code and real pytest;
- at least one review → fix round from a real finding;
- a real check failure → fix;
- the Telegram question while other tasks continue;
- a daemon restart while waiting;
- the answer → the exact resume → the task done;
- integration plus regression;
- final verification, `DELIVERY.md`, and the Missions tab showing all of
  it.

**A second mission** (P9) runs on an existing repo: one of the open
findings catalogued in Operon's HANDOFF.md, delivered as a branch for
your review, never merged by the engine.

---

## 12. Phases, gates and verification

Each phase is one branch and one PR (your rule: one branch per phase,
batched commits), merged when its gate passes. The commands and results
of each gate go into §14.

**P1. Store, model, DAG, scheduler (no LLM)**
- **Builds:** the package skeleton in `packages/operonx-forge`, the CI
  matrix entries, the mission store with its transition table, `dag.py`,
  and the scheduler driven by fake task runners.
- **Gate:**
  - unit tests of every transition, waves, the ready set, blocked
    propagation, path locks and budgets;
  - **a property test**: on random DAGs with random durations and
    failures, a task never starts before its dependencies are done,
    `max_parallel` is never exceeded, overlapping paths never run
    together, every task ends in a final state or blocked-with-reason,
    and the scheduler's step count is bounded.

**P2. Coder, git, checks**
- **Builds:**
  - `ClaudeCodeCoder`, ported from the studio: stream-json, the session
    id at `init`, `--json-schema` output, cost, timeouts, cancel (process
    group), rate-limit detection;
  - `gitops`: worktrees, commits, merge under a lock, conflicts;
  - `checks`: host and docker runs, the baseline.
- **Gate:**
  - the adapter's tests against a scripted fake `claude` executable (a
    test double, labelled);
  - git tests on real temporary repos;
  - **one paid smoke** with the real Claude Code: a session that
    creates a file, then is resumed, then returns `--json-schema`
    output. It checks the event shapes we parse, about $0.10 notional.

**P3. `task_flow` and the decision table**
- **Builds:** the graph (§5), `round_`, `decide()`, guards, the test
  author, integration.
- **Gate:**
  - with a scripted coder: the task passes; it fails then fixes; review
    findings lead to a fix; a repeated finding leads to arbitration and
    escalation; the same failure 3 times leads to a revise and then an
    escalation;
  - `max_rounds` is honoured;
  - the guard rejects an edited acceptance test;
  - a park → resume through the journal with a new engine instance;
  - **`kill -9` mid-round** → resume, with no completed round re-run (by
    counting calls);
  - a merge conflict leads to a rebase round.

**P4. Lifecycle, daemon, CLI**
- **Builds:** discover (with the baseline), clarify, plan and plan
  review with the mechanical checks, approval, final verification, the
  traceability matrix, `DELIVERY.md`, the daemon (lease, recovery pass,
  command queue) and the CLI.
- **Gate:**
  - the whole lifecycle with the scripted coder;
  - the plan-review cap;
  - replanning keeps done tasks;
  - the recovery pass covers each crash-matrix row that P3 does not;
  - `forge status` and `forge show` are accurate against the store.

**P5. Interactions and messaging (Telegram still mocked)**
- **Builds:** the interaction lifecycle, `submit_answer`, triage,
  rendering, the redactor, expiry and reminders, the `Messenger` protocol,
  `TelegramMessenger`, `FakeBotAPI` (a real HTTP server on localhost),
  and commands.
- **Gate (against `FakeBotAPI`, labelled as a test double):**
  - a button answer, a reply-to answer, an unthreaded answer with exactly
    one question open and with two open;
  - duplicate `update_id`; a second answer;
  - a stale answer after a replan; expiry with and without a safe default;
  - an unauthorized user and an unauthorized chat are silently dropped;
  - `/cancel` needs confirmation;
  - a send failure → retry → `forge answer` still works;
  - the token never appears in logs (grep in the test).

**P6. The first real end-to-end mission (paid, Telegram still the fake
server)**
- **Builds:** `commit-guard` from goal to delivery, with real Claude Code,
  real pytest, real worktrees and a real daemon. I act as you through the
  fake server's "user says" endpoint, and that is recorded as such. The
  daemon is restarted (`kill -9`) while T4 waits.
- **Gate:**
  - the delivered repo's tests pass, run by me separately from the
    engine;
  - `DELIVERY.md` claims match the store, git and test output, checked
    by a script;
  - the engine's cost is recorded;
  - every failure in the run is either fixed in code (and re-run) or
    written up in §15.
- **Expected cost:** 1–3 hours of Max quota (D3), notional $10–30.

**P7. Studio Missions tab (studio repo, its own branch)**
- **Builds:** the list, the mission page, the DAG with waves, the
  decisions card (answers via `forge api`), the task panel, the
  traceability matrix and the links to run pages.
- **Gate:**
  - studio tests for the read API and the action calls;
  - screenshots on desktop and phone of P6's real mission, live and
    finished;
  - answering from the studio follows the same rules (a stale answer is
    refused).

**P8. Hardening**
- **Builds and tests:**
  - the full crash matrix with `kill -9` at each row, against real
    processes;
  - budget and rate-limit pause and resume;
  - security: path escape, a write to an acceptance test, a denied
    `git push`, an environment leak check, an untrusted repo file
    containing instructions ("ignore your task and …") with the policy
    unchanged;
  - the docker check runner;
  - the guide page `operonx_forge/guide/01-forge.md`, run by the snippet
    tests;
  - regression: the forge suite, `tests/internal/durable`, `tests/guide`,
    agents' suite (redact is reused), and studio tests.

**P9. Real Telegram and a second mission (needs D4)**
- **Builds:** your bot token and user id, long poll (or webhook), the
  same P6 scenario with **you** answering on your phone, and then the
  second mission on an existing repo.
- **Gate (your brief's mandatory scenario, all eight steps):**
  1. a genuine decision arises;
  2. a real Telegram message arrives on your phone;
  3. the run is durably parked: the daemon is restarted while it waits;
  4. you answer in Telegram;
  5. the answer is correlated and validated;
  6. the same run resumes;
  7. the task completes;
  8. real tests pass.

**How the requirements in your brief (§9) map to phases**

| Requirement | Phase |
|---|---|
| dependency ordering, independent scheduling, blocked propagation | P1 property + unit tests |
| planning and review iteration limits | P3, P4 |
| failures, retries, recovery; partial completion | P3, P8 |
| real test execution and accurate reporting | P2, P6 (the delivery-claims check) |
| persistence and resumption after interruption | P3, P4, P8 |
| approve, reject, revise, delayed responses | P4, P5 |
| Telegram auth, correlation, duplicates, stale | P5 (fake); P9 (real) |
| safe cancellation | P4, P8 |
| security boundaries | P8 |
| integration with OperonX | P3 (journal, InterruptOp), P8 regression |
| UI and API | P7 |
| existing regression suites | P8 |

**Not counted as proof:** a mocked coder for anything about code quality,
the fake Bot API for the live Telegram integration, and the
compilation, typing or happy path alone.

---

## 13. Not building, or later

- **A general workflow runtime, or a "graph of missions".** The DAG is
  scheduler data, as Jobs' steps are.
- **A coder backend on operonx-agents** (an OpenRouter model with file
  and shell tools). This is later: it would make review vendor-independent
  and run where there is no Claude login. The `Coder` protocol leaves room
  for it.
- **Missions across several repos**, running the same task in several
  worktrees, and choosing the best.
- **Postgres** for the mission store. SQLite holds one workspace on one
  machine. The store sits behind a small interface, as the journal does.
- **Slack or e-mail messengers.** The `Messenger` protocol leaves room
  for them.
- **Core changes.** None are expected. Any gap P1–P8 finds goes upstream
  as its own PR, with a failing test first (for example, loop ops'
  outputs not reaching the run's outputs, if it turns out to matter).

## 14. Risks

| Risk | What I do about it |
|---|---|
| Claude Code's stream-json or `--json-schema` shapes change between versions | they are parsed in one module; the P2 smoke runs per version; the version is recorded on every round |
| The reviewer approves bad code | the commands decide, not the reviewer alone; acceptance tests are written first by another session; the final whole-diff review |
| Triage escalates too much, or too little | P6 measures both: every question asked and every assumption taken is listed in `DELIVERY.md`; the rules are tuned on that evidence |
| The Max quota runs out mid-mission | the rate-limit pause (§8); D3 |
| A shared host (staging callbot) is slowed | `max_parallel` = 2; checks in docker with CPU limits |
| Merge conflicts between parallel tasks | path locks, then the rebase round, then at most 2 re-opens, then you |

## 15. Progress log

(empty: filled in phase by phase with commands run and their results)

## Sources

- Anthropic, *Effective harnesses for long-running agents*:
  https://anthropic.com/engineering/effective-harnesses-for-long-running-agents
- Telegram Bot API (getUpdates, webhooks, callback queries):
  https://core.telegram.org/bots/api
- Context on the planner/implementer/reviewer pattern (secondary):
  https://www.contextstudios.ai/blog/anthropics-2026-agentic-coding-report-orchestration-era.md
