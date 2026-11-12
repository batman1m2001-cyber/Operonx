# Projects: operonx-project

**operonx-project** keeps a project's requirements and where it stands. The
requirements are a living BRD in `docs/brd/` (the sources, the requirements,
the checklist); the work is planned in `docs/*_PLAN.md` files; `status`
reads both, and the board, into one document. The work itself is done in a Claude Code
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
verified row cites, and the board.

## The board

`docs/brd/board.md` holds one card per task, in git:

```markdown
### C4 · gold jump alert · doing
req: NEWS-07 · after: C2
files: src/news/rules.py, tests/news/test_rules.py
done: tests/news/test_rules.py::test_gold_jump
```

Four fields (`req`, `after`, `files`, `done`) and four states (`todo`,
`doing`, `needs you`, `done`). The rest is read, not written: the lane from
`files` (`web/` UI, `routes` web, `ops`/`rules`/`graph` core,
`store`/`source`/`jobs` data, `prompts`/`agent` AI, the rest platform), the
kind from `req` (a requirement is *build*, none is *fix*), and whether a
card waits (an `after` card not done). Text under the fields is the card's
note: the question, when it needs the owner.

```python
from pathlib import Path

from operonx_project import board

repo = Path("demo")
board.put(repo, title="the rows", files=["src/export/ops.py"], done=["tests/export/test_ops.py::test_rows"])
board.put(repo, title="the export service", after=["C1"], files=["src/export/routes.py"])
board.move(repo, "C1", "doing")
c = board.move(repo, "C2", "needs you", note="CSV or XLSX?")
print([(x["id"], x["state"], x["lane"], x["waits"]) for x in board.view(board.read(repo))["cards"]])
assert c.state == "needs you" and board.read(repo)[1].note == "CSV or XLSX?"
```

`move` refuses `doing` while an `after` card is not done, and `done` for a
card with no `done` test (the studio runs them first). A card that adds a
file outside a feature's fixed names lands in `needs you` until the owner
says go.

```bash run
operonx-project board --repo demo             # the cards, with lane and what each waits on
operonx-project board C1 done --repo demo     # move one
```

## Cycles

A plan doc under `docs/` says why and how. Where it stands is its **cycle**:
a `##` section of `board.md` whose cards are the plan's phases. Cards before
any `##`, or under `## Backlog`, are the Backlog. Card ids stay global, so
`after:` works across cycles.

```markdown
## EX · CSV export · open
plan: docs/EXPORT_PLAN.md · opened: 2026-11-12

### C3 · the download button · doing
files: web/export.js
```

`close` refuses while a card in the cycle is not done: move each to the
Backlog or another open cycle first. A closed cycle stays in the file, its
cards as heading lines only, and `status` skips it.

```python
from pathlib import Path

from operonx_project import board

repo = Path("demo")
board.open_cycle(repo, "EX", "CSV export", plan="docs/EXPORT_PLAN.md")
board.put(repo, title="the download button", cycle="EX", files=["web/export.js"], done=["tests/test_web.py::test_dl"])
board.put(repo, "C2", cycle="EX")
try:
    board.close(repo, "EX")
except board.BoardError as exc:
    print(exc)  # cycle EX has cards not done: C2, C3. ...
board.move(repo, "C3", "done")
board.put(repo, "C2", cycle="Backlog")
y = board.close(repo, "EX")
assert y.state == "closed" and [(c.id, c.cycle) for c in board.read(repo)] == [("C1", ""), ("C2", ""), ("C3", "EX")]
```

```bash run
operonx-project cycle open NX "Next round" --repo demo   # --plan docs/NEXT_PLAN.md
operonx-project put C2 --cycle NX --repo demo            # a card into a cycle; put --title T adds one
operonx-project board --repo demo
```

## Where the project stands

`operonx-project status` (and the api's `{"op": "status"}`) puts the whole
project in one document: the checklist's counts and its open rows, the
board's cycles and cards, the cards that need you, the cards in progress
with one sentence each, and the last commits. It only reads, and every
sentence is written by code from the data, so the studio's screen and its
assistant never disagree:

```python
from pathlib import Path

from operonx_project.status import status, text

doc = status(Path("demo"))
print([y["id"] for y in doc["board"]["cycles"]])  # ['NX', 'EX']: EX is closed
assert doc["board"]["columns"]["needs you"] == ["C2"] and doc["working_on"] == [] and text(doc).startswith("Checklist:")
```

```bash run
operonx-project status --repo demo          # the same, in plain lines; --json for the document
```

## The standard

What a project needs for the studio to read it: two items, in the status
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

```python
from pathlib import Path

from operonx_project.status import status

doc = status(Path("demo"))
assert [x["key"] for x in doc["standard"]] == ["brd", "check"]
print([(x["key"], x["fix"][:40]) for x in doc["standard"] if not x["ok"]])
```

## Where things live

| Where | What |
|---|---|
| `docs/brd/` | what the product must do, and how far it is: the sources, the requirements, the checklist (`requirements/requirements_traceability.md`), the questions |
| `docs/brd/board.md` | the board: one card per task |
| `.operonx/project/` | outside git: the extraction's journals, `settings.toml` |

`settings.toml` holds this machine's models:

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
cited tests all pass becomes `implemented_verified`. Nothing is committed: read
`docs/brd/`, then commit it.

A folder inside a bigger repo is a project of its own when it is opened as
one (`--repo monorepo/service`): its own `docs/brd/` and its own `.operonx/project/`.
