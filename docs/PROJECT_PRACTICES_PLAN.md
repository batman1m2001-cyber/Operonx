# Project practices: how a project is built, declared once and enforced by the builder

**Status:** 2026-10-20. The owner asked for this ("act as a solution architect … carry it through"),
so it is approved. Every claim about today below was read in the code (Operon `487e7917`,
operonx-studio `72866a3`) or measured in the tipcalc runs of `PROJECT_BUILDER_PLAN.md`.

## 1. What the builder does today, and where it falls short

The builder (operonx-project) already runs a disciplined loop:
1. A survey and a baseline of the repo's own checks.
2. Requirement rows.
3. A plan of cards (EARS criteria, dependencies, paths), reviewed, then approved.
4. Per card: acceptance tests written *before* the code by their own session, plus held-out tests
   the implementer never sees.
5. The repo's checks judged against the baseline, then a review, an arbiter and the held-out tests.
6. A whole-milestone verification, then Ship.

Most of what Agile and TDD ask for is already there, in substance if not in name. A milestone is a
sprint with a goal and a backlog. DELIVERY.md and the PR are its review. Acceptance tests come first.

The runs found five gaps that no amount of prompting closed:

| # | Evidence (tipcalc, 2026-10-19) | Cause in the builder |
|---|---|---|
| G1 | m1 passed its tests by swapping an op's class for a private `FuncOp` subclass and setting `state._run_policy` (`src/tipsplit/ops.py`). Four reviews passed it. | AGENTS.md states the architecture ("imports go one way: app → graph → ops → helpers"), but only an LLM reviewer reads it. Nothing **checks** it: `operonx lint` has no import or private-API rule. |
| G2 | About $3 of $18 went on tests no implementation could pass: a direct op call that raises (rebuild C3, both test sets), `$state` equality (m2/C6, 6 rounds), the method in `serve --list` (m2/C3, a replan). | Each was fixed by the owner's answer. The answer died with the card: the rebuild repeated m1's misconception a day later. |
| G3 | Tests pinned incidentals: the matrix's row count, a question's id, a docstring's wording. They broke m2's final verification and re-opened C1. | The test author is not told what a test must not pin. |
| G4 | m1 cost $10.22 and the same cards rebuilt cost $3.04. Nothing reported why. | The store holds cost per stage, rounds per card, questions and failure signatures, but no report reads them. |
| G5 | Every BRD has a `GLOSSARY.md`, which is never used. | No session reads it: the code's names drift from the BRD's terms. |

## 2. Decisions

### D1. One committed file: `docs/brd/practices.toml`

It declares how this project is built. It is in git (the team shares it, a rebuild reads it) and next
to the board, because `docs/brd/` is "how the product is built". `.operonx/project/settings.toml`
stays this machine's file (sandbox, models).

- Every section is optional. A project without the file runs exactly as today (backward compatible).
- `operonx-project practices` prints the effective practices and validates the file.
  `--init <preset>` writes a starter.
- Presets: `operonx` (the layer rule AGENTS.md already states) and `clean` (Clean Architecture's four
  rings for a plain Python project). A preset is only a starting file; the project edits it.

```toml
[tdd]
mode = "acceptance"        # acceptance (today) | strict | off

[architecture]
style = "clean"            # told to every session; the rules below are what is checked
private = "forbid"         # no other package's _private names or attributes
layers = [                 # outermost first: a module imports its own layer and the ones after it
  { name = "app",    paths = ["app/**"] },
  { name = "graph",  paths = ["src/*/graph.py"] },
  { name = "ops",    paths = ["src/*/ops.py"] },
  { name = "domain", paths = ["src/*/_*.py"] },
]
forbid = [ { layer = "domain", imports = ["operonx", "operonx.*"], why = "the domain is plain Python" } ]

[[contexts]]               # DDD bounded contexts: another context is reached only through its public modules
name = "billing"
paths = ["src/billing/**"]
public = ["src/billing/graph.py"]

[language]
glossary = "docs/brd/GLOSSARY.md"   # its terms go to every session: name things with them

[done]                     # Definition of Done: more checks every card must pass
checks = [ { name = "types", cmd = "uv run mypy src" } ]

[lessons]
file = "docs/brd/LESSONS.md"        # what the milestones taught: every session reads it
```

### D2. Architecture is a check, not a request (G1)

`operonx-project arch` reads the practices and parses every Python file with `ast` (no dependency).
It reports each violation as its own failure id (`FAILED arch:layer:src/x/ops.py->app.main`).

- **The check:** the architecture check is an ordinary `Check` in the milestone's baseline, run with
  the engine's own Python. So the machinery that exists judges it unchanged: the card baseline,
  every round, the merge regression check and final verification. A violation already in the
  baseline is the past and does not cost a card a round; a new one does.
- **The rules:**
  - **layers:** the dependency rule;
  - **forbid:** framework independence of an inner layer;
  - **contexts:** bounded contexts reached only through their public modules;
  - **private:** another top-level package's `_names` imported, or `obj._attr` touched on an object
    that is not `self` or `cls`.

  The `private` rule is what G1's hack breaks. Imports are resolved to files from `src/` and the repo
  root. An import of something outside the repo only matters to `forbid`.
- **Other languages:** `[architecture] command = "…"` runs any tool (import-linter, dependency-cruiser)
  as the same check.

Rejected: an LLM "architecture reviewer". G1 shows reviewers miss it, and a check costs nothing per
round.

### D3. TDD has three modes (G2)

- **acceptance** (default, today): acceptance tests are written before the code, plus held-out tests.
- **strict**: as acceptance, and the acceptance tests must **fail on the card's base before any code
  is written** (red, then green). A test that already passes tests nothing new: the author writes it
  again once, told so, and the card records the red evidence (how many failed, how).
- **off**: no acceptance author and no examiner. The card is judged on the repo's checks and the
  review. It is for spikes and docs-heavy work, and costs less.

Rejected: "every code change must touch a test file". The acceptance tests already cover the card;
the rule would make the implementer write filler tests.

### D4. Lessons: what the owner taught is kept (G2, G3)

When a milestone is delivered, a **retrospective** stage runs.
- **The report**, from the store and with no model: cost by stage and by card, rounds per card,
  questions and their answers, rewritten tests, re-opened cards, fix cards and failure signatures.
  It goes into DELIVERY.md and the Board.
- **One Sonnet call** reads that report and proposes at most five **lessons**. A lesson is a rule
  that would have saved a round, with its evidence (for example: "a direct op call never raises;
  assert through `Operon(..., errors='raise')`. C3, Q-04").
- **Where they go:** lessons are appended to `docs/brd/LESSONS.md` on the milestone's branch, so the
  owner reviews them in the PR like any other change. Every coder session reads the file.

Rejected: a blocking "keep these lessons?" question. Ship is already the owner's approval of
everything on the branch.

### D5. Every session knows the practices (G3, G5)

`standing_rules` already appends owner.md and AGENTS.md to every session. It also appends:
- the practices, rendered as instructions (the style, the layers in words, the TDD mode);
- the glossary's terms;
- the lessons.

The test author's and the examiner's prompts gain one rule: assert what the rows promise, never
incidentals (a count of rows, an id, a docstring's wording).

### D6. Definition of Done is more checks

`[done] checks` are added to the milestone's checks at discovery, beside the repo's own. Coverage
gates, type checks and a doc build all fit, without code for each.

### D7. The assistant chooses, explains and sets the practices

The studio assistant gets:
- **`board_practices`:** read the effective practices, plus the architecture check run on the checkout
  (each violation); set them (validated by the engine, written and committed on the checkout's
  branch, that file only); write a preset.
- **knowledge.md, a decision guide:** which practice to recommend when, and what each one costs and
  saves. For example: strict TDD costs one test run per card; `off` is for spikes; Clean Architecture
  layers suit a project with a real domain, not a 100-line tool; contexts suit a project with two or
  more domains.
- **`board_status`:** the milestone's retrospective and lessons, so the assistant can say what the
  last milestone taught.

The Board shows the practices as chips (TDD strict · clean, 4 layers · 2 contexts) and the
architecture check's count of violations.

### Rejected outright, with reasons

- **Scrum's ceremonies** (stand-ups, story points, velocity planning, a sprint length): the workers are
  sessions that start fresh, and a milestone already is the sprint. What Scrum gives that is worth
  having here is a Definition of Done (D6) and a retrospective that changes the next sprint (D4).
- **A dependency-approval gate:** there is no evidence yet. The `shared` warning and the reviewer see
  `pyproject.toml` changes. It is the first candidate if a run shows a need.
- **Methodology profiles** ("agile", "waterfall"): W1 (plan everything, push) and W2 (rolling horizon,
  pull) already are the two that matter.
- **Coverage built in:** `[done] checks = [{cmd = "pytest --cov-fail-under=80"}]` is one line.

## 3. Phases

Each phase is one branch per repo, merged when its gate passes. The tests use scripted coders. Model
calls happen only in P6, on Sonnet.

| Phase | What | Gate |
|---|---|---|
| P1 | `practices.py`: load, validate, render; `operonx-project practices [--init]`; standing rules gain practices, glossary and lessons; `[done]` checks at discovery; the test author's and examiner's rule (D5) | Unit tests. A project without the file gives the same baseline checks and the same session prompt as today. |
| P2 | `contracts.py` + `operonx-project arch`: layers, forbid, contexts, private, command; the architecture check in the baseline | Unit tests per rule. On tipcalc `main` it reports the `_RaisingOp` access. On the four adopted projects and tipcalc, with the `operonx` preset, every violation is read by hand: none is false. |
| P3 | TDD modes: strict's red check (re-author once), `off` | Scripted: a vacuous test is re-authored; red evidence recorded; `off` calls no author. |
| P4 | Retrospective: the report in DELIVERY.md, the lessons call, LESSONS.md on the branch | Scripted: a milestone with a rewritten test proposes a lesson that lands in LESSONS.md, and the next session's prompt carries it. |
| P5 | Studio: `board_practices`, the decision guide, retro in `board_status`, Board chips; the API's `practices` op | Studio tests; desktop and phone screenshots. |
| P6 | End to end, through the assistant: chat sets tipcalc's practices (operonx preset, strict TDD); a request replaces `_RaisingOp`. Then the guide page, the init template (`practices.toml`, `LESSONS.md`), operonx release | The run: the arch check shows the violation in the baseline and as fixed at the end; the red evidence; lessons committed; Ship. Sonnet, cap $5. |
