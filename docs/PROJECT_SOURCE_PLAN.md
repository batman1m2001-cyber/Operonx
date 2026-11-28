# Project source plan: the folder comes from GitHub, the `.env` from studio's state

Status: **not started** (written 2026-11-28). Repositories: operonx-studio (most of it), Operon (S5 only).
Pictures: [the flow](https://claude.ai/artifact/YVWrqt8zP2bnnnBF85TUSR) (it still shows secrets in
Postgres; this document is the current one), [the screens](https://claude.ai/artifact/CtANVWpQJdUGfNbaoKxBzq).

## 0. The rule

A project is still the standard operonx folder (`operonx/guide/05-project-layout.md`). Nothing in
the layout changes, and every CLI keeps working on it. What changes is where the folder and its
`.env` come from on a hosted studio:

```text
code        GitHub repo                        studio clones it
.env        ~/.operonx/secrets/, outside it    studio copies it into the clone
run data    ClickHouse / Postgres              as today
```

The clone on the server is a cache: delete it and studio makes it again, secrets included.

Opening a local folder (`operonx studio` on a laptop) stays as it is. A cloned project and an
opened folder are the same thing to the rest of studio.

No new database. Studio's state stays one folder (`OPERONX_STUDIO_STATE_DIR`, default
`~/.operonx`); moving studio to another server is copying that folder.

## 1. Audit (2026-11-28, studio `e42aa90`, Operon `2ce412c4`)

| Fact | Where |
|---|---|
| Studio knows a project as a path; the id is a digest of the path | `operonx_studio/registry.py`, `~/.operonx/studio.json` |
| Secrets are the project's `.env`; studio already edits it and reports names, never values | `POST /api/p/{pid}/env`, `envstatus.py` |
| `resources.yaml` takes secrets as `${VAR}`; the hub stores YAML or JSON only | `operonx/core/registry/storage/` |
| Other code reads the project's `.env` from disk | `operonx/prompt_store.py` `dsn_of`, `operonx.bootstrap()` |
| Studio already keeps git worktrees per task | `~/.operonx/worktrees/` |
| `~/.operonx/projects.json` in the live state holds `/tmp/pytest-…` paths | a test writes to the real state dir |

Three decisions follow:

- **`resources.yaml` stays in the project.** It is code: it says which model and store a graph uses.
- **The clone gets a real `.env` file.** Several readers expect the file, and the standard layout has it.
- **Secrets are files, not rows in Postgres.** Reading encrypted secrets from Postgres needs a DSN
  and a key, and both would sit in a file on this same server. With one studio server that adds a
  key to lose and protects nothing more. See §4 for when to revisit.

## 2. After

```text
GitHub: org/callbot                      the standard layout, unchanged
├── operonx.toml                         + [environments.<name>] url = "…"   (S5, optional)
├── resources.yaml  pyproject.toml
├── app/  src/  prompts/  datasets/  tests/
└── .env.example                         key names only

~/.operonx/
├── studio.json                          each project: a folder path, or a repo URL
├── workspaces/<project-id>/<branch>/    a clone per branch; its .env is a copy
└── secrets/<project-id>/<environment>.env     0600; dev.env by default
```

One setting, an env var of the studio process: `OPERONX_STUDIO_GITHUB_TOKEN` (clone and push over
HTTPS). The project id of a cloned project is a digest of the repo URL, so it is the same on any
server.

## 3. Phases

Each phase ships on its own and leaves studio working.

| | What | Done when |
|---|---|---|
| **S1** | Secrets outside the project. The existing env screen saves to `secrets/<id>/<environment>.env` and copies it into the project as `.env`; an environment picker; a "used by" column and missing names from `resources.yaml` and `.env.example`. | A secret set in studio survives deleting the project folder. Values never appear in a response or a log. |
| **S2** | Add a project by repo URL. Clone into `workspaces/`, record the URL in `studio.json`, show the missing secrets. | callbot added from its URL on an empty state dir runs a graph. |
| **S3** | Branches. Branch picker, Pull, Save (local commit on a work branch), Push (a button; never the default branch), link to open a pull request. | An edit made in studio arrives on GitHub as a branch, only after Push. |
| **S4** | Move the current projects over: add each by URL, copy its `.env` to `secrets/` once. Fix the test that writes `projects.json` into the real state dir. | The six projects open from `workspaces/`; the old folders are no longer read. |
| **S5** | Deployment status, read only. Operon: `describe()` reports the git commit and the version; `operonx.toml` may name a URL per environment. Studio: asks each URL and shows commit, age, health. | `callbot · staging · <commit> · healthy` is shown; an environment with no URL shows "not deployed". |

Defaults: a new project has one environment, `dev`, with no URL, and runs inside studio. No branch
is assumed to mean an environment.

## 4. Not in this plan

- **Secrets in Postgres.** Worth it when there is a second studio server, a need to record who
  changed a secret, or users who must not have shell access to the server. None holds today.
- Studio does not deploy. Each project's CI/CD does; a standard deploy template is a later plan.
- A GitHub App. One token is enough for one team.
- A Postgres backend for the resource hub.

## 5. Risks

| Risk | Answer |
|---|---|
| Secrets are plain files on the server | As today, but in one 0600 folder outside every repo. S2 refuses a repo whose `.gitignore` does not cover `.env`. |
| The state folder is lost | It is small: back up `studio.json` and `secrets/`. Clones are not backed up. |
| Two people edit the same branch in studio | They share the clone, as they share the folder today. Not solved here. |
