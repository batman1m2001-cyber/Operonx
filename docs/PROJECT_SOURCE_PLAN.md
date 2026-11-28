# Project source plan: the folder comes from GitHub, the secrets from Postgres

Status: **not started** (written 2026-11-28). Repositories: operonx-studio (most of it), Operon (S5 only).
Picture of the same plan: <https://claude.ai/artifact/YVWrqt8zP2bnnnBF85TUSR>

## 0. The rule

A project is still the standard operonx folder (`operonx/guide/05-project-layout.md`). Nothing in
the layout changes, and every CLI keeps working on it. What changes is where the folder and its
`.env` come from on a hosted studio:

```text
code        GitHub repo                      studio clones it
.env        studio's Postgres, encrypted     studio writes it into the clone (0600, gitignored)
run data    ClickHouse / Postgres            as today
```

The clone on the server is a cache: delete it and studio makes it again.

Opening a local folder (`operonx studio` on a laptop) stays as it is. A cloned project and an
opened folder are the same thing to the rest of studio.

## 1. Audit (2026-11-28, studio `e42aa90`, Operon `2ce412c4`)

| Fact | Where |
|---|---|
| Studio knows a project as a path; the id is a digest of the path | `operonx_studio/registry.py`, `~/.operonx/studio.json` |
| Secrets are the project's `.env`; studio already edits it and reports names, never values | `POST /api/p/{pid}/env`, `envstatus.py` |
| `resources.yaml` takes secrets as `${VAR}`; the hub stores YAML or JSON only | `operonx/core/registry/storage/` |
| Other code reads the project's `.env` from disk | `operonx/prompt_store.py` `dsn_of`, `operonx.bootstrap()` |
| Studio already keeps git worktrees per task | `~/.operonx/worktrees/` |
| `~/.operonx/projects.json` in the live state holds `/tmp/pytest-…` paths | a test writes to the real state dir |

Two decisions follow from this:

- **`resources.yaml` stays in the project.** It is code: it says which model and store a graph uses.
- **Studio writes a real `.env` file into the clone** instead of passing env vars to each process.
  Several readers expect the file, and the standard layout already has it.

## 2. After

```text
GitHub: org/callbot                      the standard layout, unchanged
├── operonx.toml  resources.yaml  pyproject.toml
├── app/  src/  prompts/  datasets/  tests/
└── .env.example                         key names only

~/.operonx/workspaces/<project-id>/<branch>/     a clone per branch, with its .env

Postgres (OPERONX_STUDIO_DB)
├── projects       id, name, repo_url, default_branch
├── environments   project_id, name, service_url      one row "dev" by default
└── secrets        project_id, environment, key, value (encrypted)
```

Settings, both env vars of the studio process: `OPERONX_STUDIO_DB` (the DSN),
`OPERONX_STUDIO_SECRET_KEY` (encrypts secret values), `OPERONX_STUDIO_GITHUB_TOKEN` (clone and
push over HTTPS). With no `OPERONX_STUDIO_DB`, studio behaves exactly as today.

The project id of a cloned project is a digest of the repo URL, so it is the same on any server.

## 3. Phases

Each phase ships on its own and leaves studio working.

| | What | Done when |
|---|---|---|
| **S1** | Secret store. The three tables; the existing env screen saves to Postgres and writes the `.env`; an environment picker. For an opened folder with no DB, it edits `.env` as today. | A secret set in studio survives deleting the clone. Values never appear in a response or a log. |
| **S2** | Add a project by repo URL. Clone into `workspaces/`, insert the `projects` row, show which `${VAR}` and `.env.example` names have no value yet. | callbot added from its URL on an empty state dir runs a graph. |
| **S3** | Branches. Branch picker, Pull, Save (local commit on a work branch), Push (a button; never the default branch), link to open a pull request. | An edit made in studio arrives on GitHub as a branch, only after Push. |
| **S4** | Move the current projects over: add each by URL, copy its `.env` into the store once. Fix the test that writes `projects.json` into the real state dir. | The six projects open from `workspaces/`; the old folders are no longer read. |
| **S5** | Deployment status, read only. Operon: `describe()` reports the git commit and the version. Studio: reads `service_url` of each environment and shows commit, age, health. | `callbot · staging · <commit> · healthy` is shown; an environment with no URL shows "not deployed". |

Defaults: a new project has one environment, `dev`, with no service URL, and runs inside studio.
No branch is assumed to mean an environment.

## 4. Not in this plan

- Studio does not deploy. Each project's CI/CD does; a standard deploy template is a later plan.
- Users and conversations stay in sqlite.
- A GitHub App. One token is enough for one team; revisit if studio gets outside users.
- A Postgres backend for the resource hub.

## 5. Risks

| Risk | Answer |
|---|---|
| The `.env` in a clone is a secret on disk | Same as today, but now 0600, gitignored, and rewritten from the store; S2 refuses a repo whose `.gitignore` does not cover `.env`. |
| Losing `OPERONX_STUDIO_SECRET_KEY` loses every secret | Say so on the settings screen; the key is backed up with the DSN. |
| Two people edit the same branch in studio | One clone per branch, so they share it, as they share the folder today. Not solved here. |
