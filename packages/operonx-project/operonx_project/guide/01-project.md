# Projects: operonx-project

**operonx-project** keeps a project's requirements and where it stands. The
requirements are a living BRD in `docs/brd/` (the sources, the requirements,
the checklist); the work is planned in `docs/*_PLAN.md` files; `status`
reads both into one document. The work itself is done in a Claude Code
session or the studio's assistant.

```bash norun
pip install -e packages/operonx-project      # or: uv pip install -e packages/operonx-project
```

`operonx project …` and `operonx-project …` are the same command.

## A project and its BRD

```bash run
operonx-project new demo --name demo
operonx-project brd check --brd demo/docs/brd
```

`new` makes a git repo with an empty `docs/brd/`. A requirements document
in any form (Word, PDF, Markdown, photos) is registered as a source, then
extracted into requirements with every quote checked against the source:

```bash norun
operonx-project brd register brd.docx --brd demo/docs/brd            # SRC-01; --private: by hash, never committed
operonx-project brd extract SRC-01 brd.docx --prefix BRD --title "Client BRD" --brd demo/docs/brd
```

`brd check` checks the links, the ids, the checklist matrix, the tests each
verified row cites, and the plans' status blocks.

## Plans

Work is planned in a Markdown file under `docs/`, and the plan says where it
stands in a block at its top. A phase's status changes in the commit that
finishes it, as a checklist row does, so the status stays true whoever did
the work:

```python
from pathlib import Path

from operonx_project.plans import find

(Path("demo") / "docs").mkdir(exist_ok=True)
(Path("demo") / "docs" / "EXPORT_PLAN.md").write_text("""---
title: CSV export
status: in_progress            # plan | in_progress | done | parked
phases:
  - {id: E1, title: The rows, status: done}          # open | in_progress | done | skipped
  - {id: E2, title: The export service, status: in_progress}
rows: []                       # the checklist rows it serves
updated: 2026-10-23
---

# CSV export
""")
plans, errors, warnings = find(Path("demo"))
[plan] = plans
print(plan.title, plan.status, f"{plan.done}/{len(plan.phases)}", plan.current.id)  # CSV export in_progress 1/2 E2
assert errors == [] and plan.current.title == "The export service"
```

```bash run
operonx-project plans --repo demo        # every plan and where it stands; exit 1 when a block is wrong
```

A plan also goes on the board, through `operonx-project api` and its
`{"op": "plan", "path": "docs/EXPORT_PLAN.md"}` (`"text"`: the doc as it
stands in the worktree it is worked in, when the checkout has not got it
yet). The plan becomes a milestone and each phase a card, in the state the
doc gives it. Each later call brings the same milestone back in line with
the doc. The studio makes that call at the end of every assistant turn.

```python
from pathlib import Path

from operonx_project.api import act
from operonx_project.project import Project

doc = (Path("demo") / "docs" / "EXPORT_PLAN.md").read_text()
got = act(Project.open(Path("demo")), {"op": "plan", "path": "docs/EXPORT_PLAN.md", "text": doc})
print(got["message"])  # m1 · CSV export: 1 of 2 done (new)
assert got["ok"] and got["milestone"] == "m1"
```

## Where the project stands

`operonx-project status` (and the api's `{"op": "status"}`) puts the whole
project in one document: the checklist's counts and its open rows, the plans
and milestones in one shape, the BRD's open questions, what is in progress
with one sentence each, and the last commits. It only reads, and every
sentence is written by code from the data, so the studio's screen and its
assistant never disagree:

```python
from pathlib import Path

from operonx_project.status import status, text

doc = status(Path("demo"))
[m1] = [p for p in doc["plans"] if p["milestone"] == "m1"]
print(doc["working_on"][0]["sentence"])  # E2, phase 2 of 2: The export service
assert m1["title"] == "CSV export" and text(doc).startswith("Checklist:")
```

```bash run
operonx-project status --repo demo          # the same, in plain lines; --json for the document
operonx-project milestones --repo demo      # the board's milestones; show m1 [C1] for one
```

## The standard

What a project needs for the studio to read it: three items, in the status
document as `standard` and as one line of `text()`. Each says where it
stands and, when it is missing, what fixes it.

1. **`brd`**: `docs/brd` with its requirements and the checklist matrix. `adopt` writes one from the code.
2. **`check`**: `brd check` reports nothing. A summary table whose sets have names of their own (not the id
   prefixes) names them in `docs/brd/brd.toml`:

   ```toml
   [[groups]]
   name = "BRD numbered (BR, FR, NFR)"    # the summary row's first cell, exactly
   pattern = "BRD-(BR|FR|NFR)-"           # a regex the row ids of that set match from their start
   ```
3. **`plans`**: every `docs/*_PLAN.md` has a valid status block.

```python
from pathlib import Path

from operonx_project.status import status

doc = status(Path("demo"))
assert [x["key"] for x in doc["standard"]] == ["brd", "check", "plans"]
print([(x["key"], x["fix"][:40]) for x in doc["standard"] if not x["ok"]])
```

## Where things live

| Where | What |
|---|---|
| `docs/brd/` | what the product must do, and how far it is: the sources, the requirements, the checklist (`requirements/requirements_traceability.md`), the questions |
| `docs/brd/board/` | the board: one file per milestone, its cards and their status |
| `.operonx/project/` | outside git: the board's store (`project.db`), the extraction's journals, `settings.toml` |

When `.operonx/project/` is missing (a fresh clone), the store is rebuilt
from `docs/brd/board/`. `settings.toml` holds this machine's models:

```toml
model = "sonnet"       # every session's model
[models]
hunter = "opus"        # one role's model (surveyor, extractor, hunter); it wins over `model`
```

Every session also reads the owner's standing rules: `~/.operonx/owner.md`
(or the file `OPERONX_OWNER_RULES` names) and the project's `AGENTS.md`.

## A project that is already built: adopt

```bash norun
operonx-project adopt --repo ../shop --prefix SHOP     # --max-usd 2 by default
```

A read-only session reads the code, the docs and the tests. It writes
`docs/brd/` (made first if missing), the requirements in
`requirements/adopted_requirements.md`, and one checklist row each, as
built, partial or open. The engine then runs the tests each row cites,
with the project's `.venv` (`--python` overrides it). Only a row whose
cited tests all pass becomes `implemented_verified`. The built work
becomes milestone `m0 · baseline` on the board. Nothing is committed: read
`docs/brd/`, then commit it.

A folder inside a bigger repo is a project of its own when it is opened as
one (`--repo monorepo/service`): its own `docs/brd/` and its own store.
