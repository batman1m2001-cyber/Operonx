# App layout plan: `src/` computes, `app/` exposes

Status: **L1 + L2 DONE** (2026-11-25, #331: L1 alone left `operonx add` writing a `Service` into `src/`); **L3 DONE** (C20, which also flags `agent_service` from `operonx.agents`; 0 findings in the six projects); L4–L5 not started. Repositories: Operon (this one), mr-finance. No runtime change:
`Application` already takes whatever lists `app/main.py` hands it. This changes the convention
(guide, `operonx init`, `operonx add`, lint) and one project.

## 0. The rule

```text
src/<feature>.py     ops + graphs: what the product computes        (core)
app/<feature>.py     that feature's SERVICES, JOBS, request bodies  (how it is exposed)
app/main.py          APP = Application(...): collects app/*.py, plus app-wide pieces
web/                 the pages                                      (frontend)
```

Nothing in `src/` declares a `Service` or a `Job`. A graph is importable and testable with nothing
serving it, and `app/` is the one place that says what the product exposes.

## 1. Audit (2026-11-25, Operon `8ae039b2`)

### 1.1 What projects do: all of them already use `app/`

| Project | Services/jobs declared in `src/` | `app/` lines | Note |
|---|---|---|---|
| mr-finance | 0 | 2,467 (`main.py` 899, `web.py` 1,401) | 11 `src/<f>/bodies.py`: request contracts, imported only by `app/main.py` |
| callbot (educa-reminder-agent) | 0 | 572 | |
| qc-snatcher | 0 | 446 | `src/jobs/*` imports `last_run` only, it declares nothing |
| meeting-prep-brd | 0 | 32 | |
| meeting-prep-operonx | 0 | 40 | |
| tcb-wepro | 0 | 25 | |

Counted as files under `src/` that import `Service`/`Job` or assign `JOBS`/`SERVICES`.

### 1.2 What operonx teaches: the other way

| Place | What it says now |
|---|---|
| `operonx/guide/05-project-layout.md` | a feature's `JOBS` and `SERVICES` at the bottom of `src/<feature>.py`; split shape has `src/<f>/jobs.py`, `routes.py` |
| `operonx/guide/README.md` (l. 57) | same |
| `templates/{hello,chat,agent}/src/<feature>.py.tmpl` | each ends with `JOBS = [...]` and `SERVICES = [...]` |
| `templates/_common/app/main.py.tmpl` | `from {{feature}} import JOBS, SERVICES` |
| `templates/_common/AGENTS.md.tmpl` (l. 26–35, 109, 224) | same order, same split shape |
| `templates/_common/README.md.tmpl` (l. 5–8, 46–48) | same |
| `operonx/cli/add.py` (761 lines) | `route` and `job` write into `src/F.py`, or `routes.py` / `jobs.py` of a split `src/F/` |
| `operonx/cli/main.py` (l. 90) | `--http` help: "SERVICES in src/<feature>.py" |
| Tests | `test_add_cli.py` (9 refs), `test_init.py` (7), `test_check_cli.py` (5), `lint/test_shape.py` |
| Studio, operonx-project | nothing reads `JOBS`/`SERVICES` by name |

So the code teaches a layout no project uses.

## 2. Decisions

- **D1. `app/<feature>.py` holds a feature's `SERVICES`, `JOBS` and request bodies;** `app/main.py`
  collects them, plus what belongs to the whole app (the `web/` mount, middleware, startup). A small
  project may keep everything inline in `app/main.py`; only `src/` is checked.
- **D2. `src/` keeps its name.** We considered calling it `core/` and dropped the idea. The folder
  is an import root, so its name appears in no import (`from assistant import registry`), and
  renaming it would touch every project's operonx.toml and pyproject, the `src=("src", ".")`
  default, templates and lint, for a cosmetic gain. A folder named `core` on the import path would
  also make `import core` resolve to it. The guide keeps calling the three folders core, backend
  and frontend.
- **D3. The split shape of `src/<feature>/` loses `jobs.py` and `routes.py`:** it keeps `ops.py`,
  `graph.py`, `_<helper>.py` and `__init__.py`. Past ~300 lines, `app/<feature>.py` becomes
  `app/<feature>/` the same way.
- **D4. Imports go one way:** `app/` → `src/<feature>` → `src/_shared/`. C13 keeps skipping `app/`.
- **D5. New lint rule C20, an error: "`src/` declares no `Service` or `Job`; that is `app/`."** It
  flags a call to `Service(`/`Job(` and an import of `Service`, `Job`, `http`, `asgi`, `websocket` from
  `operonx.app`. Helpers such as `last_run`/`JobRun` stay allowed. All six projects pass it today
  (§1.1). An older project records a baseline (`[lint] baseline = {C20 = n}`), as with any rule.
- **D6. `operonx add` finds an existing list where it is,** in `app/F.py`, or in an old project's
  `src/F.py` or `src/F/routes.py`, and creates new ones in `app/F.py`. It never moves code it did not
  write. Ops and graphs still go to `src/F.py`.
- **D7. Release as 1.49.0** (minor: a new lint error). Ask before merging the bump, since merging
  publishes to PyPI.

## 3. Phases

### L1: guide and templates (Operon)
- Update `05-project-layout.md` (tree, rules, the split shape), `guide/README.md`, the AGENTS and
  README templates, and `cli/main.py`'s `--http` help.
- `templates/{hello,chat,agent}/src/<f>.py.tmpl`: drop `JOBS`/`SERVICES` and their imports.
- Add `templates/{hello,chat,agent}/app/<f>.py.tmpl` with them. `init` copies the tree, so its code
  needs no new path logic.
- `_common/app/main.py.tmpl`: `from app.<feature> import JOBS, SERVICES`.
- **Gate:** for each template × {plain, `--http`, `--http --web`}, a fresh `operonx init` passes its
  own tests, `operonx check` and `operonx lint` with 0 findings.

### L2: `operonx add` (Operon)
- `route` and `job` write the `Service`/`Job` into `app/F.py`. `--feature` keeps naming the feature.
- `add` finds an old project's list as D6 says.
- `APP` wiring imports from `app.F`.
- **Gate:** `test_add_cli.py` covers a new project, a split `src/F/`, and an old project with
  `SERVICES` in `src/F.py` (the list is found and grows, and no second list appears). In each,
  `operonx check --call` passes.

### L3: lint C20 (Operon)
- In `operonx/lint/shape.py`, next to C13–C17, plus `cli/lint.py`'s rule table.
- **Gate:** `test_shape.py` covers a `Service(` in `src/`, an import of `http`, and `last_run`
  allowed. Lint on the six projects reports no C20 findings.

### L4: release (Operon), after asking
- CHANGELOG, then bump to 1.49.0.
- **Gate:** the full suite is green and the publish workflow is green.

### L5: mr-finance
- Split `app/main.py` (899 lines) into `app/<feature>.py` per feature. `main.py` keeps `APP`, the
  web mount and the app-wide pieces.
- Move the 11 `src/<f>/bodies.py` files beside their services. Only `app/main.py` imports them today.
- Bump to `operonx>=1.49.0`. Pin `OPERONX_TRACE=local` in the test conftest in place of the
  `replace(APP.manifest, tracing=None)` workaround in `tests/test_agent_map.py` (operonx #329).
- **Gates:**
  - `Application.find(".").describe()` is identical before and after: the same services, jobs,
    graphs, ports, paths and sinks.
  - The suite is green (`pytest -n 8`, 0 failed).
  - `app/main.py` is under 200 lines.
  - Lint shows no C20 findings.
- Push to origin only. Restart the live :8030 when the owner says so.

Callbot, qc-snatcher, meeting-prep and tcb-wepro need no change (§1.1). They pick up C20 with their
next operonx bump.

## 4. Budget

- No paid calls.
- Operon, expected: templates −50/+60 lines, `add.py` about −30/+40, C20 about +40 with tests.
- mr-finance: line count about the same, moved across files. `describe()` is the proof that nothing
  else changed.
