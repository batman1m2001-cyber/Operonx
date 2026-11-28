# Project source plan: the folder comes from git, the `.env` from studio's state

Status: **S1, S2, S3 DONE** (2026-11-28: studio #278, #279, #280; write-ups in the studio repo,
`docs/project_source_plan/`). **S4 is the owner's call**, see §3.1. Repository: operonx-studio. Nothing changes in Operon.
Pictures: [the flow](https://claude.ai/artifact/YVWrqt8zP2bnnnBF85TUSR) (as built),
[the screens](https://claude.ai/artifact/CtANVWpQJdUGfNbaoKxBzq) (the mock-ups reviewed before building).

## 0. The rule

A project is still the standard operonx folder (`operonx/guide/05-project-layout.md`). Nothing in
the layout changes, and every CLI keeps working on it. What changes is where the folder and its
`.env` come from on a hosted studio:

```text
code        a git remote (any host)            studio clones it
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
| Projects are not all on GitHub: callbot is on `gitlab.tguru.vn` | `git remote` |
| callbot has one env file per environment, and they are **tracked in git**: `.env_dev`, `.env_staging`, `.env_prod`, `.env_test`. Two secret names hold a value in them (`LANGFUSE_SECRET_KEY`, `CH_TRACES_PASSWORD`) | `git ls-files` |

LEAN_PRODUCT_PLAN (2026-11-24, studio #272) took the git view, Environments and deploy out of
studio: git and deploying are the team's tools. This plan keeps that. It adds no branch picker, no
commit screen and no deploy status; only what a clone on a server cannot do without.

Three decisions follow:

- **`resources.yaml` stays in the project.** It is code: it says which model and store a graph uses.
- **The clone gets a real `.env` file.** Several readers expect the file, and the standard layout has it.
- **Secrets are files, not rows in Postgres.** Reading encrypted secrets from Postgres needs a DSN
  and a key, and both would sit in a file on this same server. With one studio server that adds a
  key to lose and protects nothing more. See §4 for when to revisit.

## 2. After

```text
git remote: org/callbot                  the standard layout, unchanged
├── operonx.toml  resources.yaml  pyproject.toml
├── app/  src/  prompts/  datasets/  tests/
└── .env.example                         key names only

~/.operonx/
├── studio.json                          each project: a folder path, or a repo URL
├── workspaces/<project-id>/             the clone; its .env is a copy
└── secrets/<project-id>/<environment>.env     0600; dev.env by default
```

Access is one token per git host, in `~/.operonx/secrets/git-tokens.env` (0600): a line
`github.com=…`, a line `gitlab.tguru.vn=…`. Studio clones and pushes over HTTPS with it. The project id of a cloned project is a digest of the repo URL, so it is the same on any
server.

## 3. Phases

Each phase ships on its own and leaves studio working.

| | What | Done when |
|---|---|---|
| **S1** | Secrets outside the project. The existing env screen saves to `secrets/<id>/<environment>.env` and copies it into the project as `.env`; an environment picker; a "used by" column and missing names from `resources.yaml` and `.env.example`. | A secret set in studio survives deleting the project folder. Values never appear in a response or a log. |
| **S2** | Add a project by repo URL. Clone into `workspaces/`, record the URL in `studio.json`, show the missing secrets. | callbot added from its URL on an empty state dir runs a graph. |
| **S3** | Pull and Push, two buttons. Pull brings the clone up to date. Push commits what was edited in studio and sends it to one branch, `studio/<project>`, never the default one, then links to open a pull request (GitHub) or merge request (GitLab). | An edit made in studio arrives on the remote as that branch, only after Push. |
| **S4** | Move the current projects over: add each by URL, copy its `.env` to `secrets/` once (callbot: its four `.env_<name>` files, then untrack them and rotate the two secrets that were committed). Fix the test that writes `projects.json` into the real state dir. | The six projects open from `workspaces/`; the old folders are no longer read. |

### 3.1 S4 was not run

- On this server the six projects' folders are the working copies: people and Claude Code edit
  them, and the studio shows those edits live. Moving the studio to clones under `workspaces/`
  would show a copy that is only as new as the last Pull. S4 fits a studio on a server where
  nobody edits; here it would be a step back. Decide per server.
- What S4 wanted for secrets already happens: since S1, opening a project copies its `.env` to
  `secrets/<id>/dev.env`.
- It needs a token per host, which only the owner can make (set in Settings › Project › Git access).
- callbot's tracked `.env_<name>` files and the two committed secrets (§1) are still to untrack
  and rotate.
- `~/.operonx/projects.json` was last written on 2026-11-11 by operonx-project, which left core
  in 1.47.0. Nothing writes it now: there is no test to fix, and the file can be deleted.

Changed while building: the project id stays the digest of the folder's path (S2 write-up);
Pull and Push are not shown under 1100 px; `.operonx/` is never pushed.

Added after S3 (studio #283, #284): a viewer sees secret names and set/missing only; an admin sets,
replaces, removes and tests a host's git token in Settings › Project › Git access, so a wrong or
expired token needs no shell (write-up: studio `docs/project_source_plan/git-access.md`).

An environment here is only a named set of secrets (`dev.env`, `staging.env`). A new project has
one, `dev`.

## 4. Not in this plan

- **Secrets in Postgres.** Worth it when there is a second studio server, a need to record who
  changed a secret, or users who must not have shell access to the server. None holds today.
- A branch picker, a commit screen, history: git is the team's tool (LEAN_PRODUCT_PLAN).
- Deploying and deploy status. Each project's pipeline does that.
- A GitHub App or OAuth. One token per host is enough for one team.
- A Postgres backend for the resource hub.

## 5. Risks

| Risk | Answer |
|---|---|
| Secrets are plain files on the server | As today, but in one 0600 folder outside every repo. S2 warns when a repo tracks a `.env*` file other than `.env.example`. |
| The state folder is lost | It is small: back up `studio.json` and `secrets/`. Clones are not backed up. |
| Two people edit the same branch in studio | They share the clone, as they share the folder today. Not solved here. |
