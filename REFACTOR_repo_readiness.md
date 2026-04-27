# Refactor Plan — Make Operonx a Ready-for-Use Public Repo

## Why

Operonx was migrated from Hush-ai's multi-package layout (hush-icore, hush-providers, hush-telemetry, hush-serve, plus equivalent Rust crates) to a **single Python package with extras** (`operonx` + `[anthropic]`, `[langfuse]`, `[otel]`, `[serve]`, ...) and a **single Rust crate** (`operonx`). The collapse is clean in the source tree, but downstream surfaces never followed:

1. **All 7 GitHub workflows are broken.** They reference `python/hush-icore/`, `rust/hush-icore/`, etc. — paths that don't exist in Operonx. Tests, docs build, version-check publishing, format/lint, Python compatibility matrix, Rust runtime, and parity checks all currently fail or no-op.
2. **No `docs/` content and no `mkdocs.yml`.** Hush had `docs/architecture/`, `docs/api/`, `docs/guide/`, plus mkdocstrings auto-build that pulls API reference from Python docstrings. Operonx ships zero published docs.
3. **No pre-commit config.** Hush had ruff (lint + format) on every commit. Contributors land unformatted code.
4. **Codecov is half-wired.** `tests.yaml` already calls `codecov/codecov-action@v4` with a `CODECOV_TOKEN`, but there's no `codecov.yml` to configure thresholds and no badge in the README.
5. **README is severely out of date.** Still references "Hush", `Hush(graph)`, `hush-icore` PyPI package, etc. — left over from before the rename. Anyone landing on the repo is misled.
6. **No CHANGELOG.** Version bumps are silent.
7. **`operonx[all]` extra is defined but never tested in CI.** Same for individual extras like `[anthropic]`, `[onnx]`, `[huggingface]`. Users discover broken extras at install time, not in CI.
8. **PyPI name collision.** The current pyproject says `name = "operonx"`, but `operonx` on PyPI is already taken by an unrelated project. Nothing in this repo could be published until we rename — addressed in [Naming decision](#naming-decision-resolved--operonx-everywhere) below.

The refactor brings the surface in line with the source tree and ships the polish that turns Operonx from "code in a repo" into "something a stranger can `pip install operonx[anthropic]` and have working in 5 minutes".

## Non-goals

- **No new features.** No new ops, no new providers, no new tracers.
- **Not implementing deferred items from other plans.** Rust OTEL backend and HTTP serve port stay deferred — unrelated to repo readiness.

## Naming decision (resolved — `operonx` everywhere)

The Python package is renamed from `operonx` to `operonx`, matching the Rust crate name. Two reasons that make this the obvious call:

1. The PyPI name `operonx` is **already taken by an unrelated project**, so we cannot publish under `operonx` even if we wanted to.
2. Operonx has **never been published**, so the rename has zero migration cost for users — only mechanical churn inside this repo.

The project is still called "Operonx" colloquially (in the README, the docs site title, the GitHub repo name). The Python package and the Rust crate share the canonical artifact name `operonx`. Users do `pip install operonx[anthropic]` and `cargo add operonx`.

Mechanical scope of the rename — covered as Step 0 in the [Migration steps](#migration-steps-suggested-order):

- `operonx/` source tree → `operonx/`.
- Every `import operonx` / `from operonx.X import Y` across `operonx/`, `tests/`, `examples/python/`.
- `pyproject.toml`: `name = "operonx"` → `"operonx"`; fix `[all]` and `[standard]` self-references (`operonx[...]` → `operonx[...]`).
- Public API strings: error messages naming `operonx.bootstrap()`, docstrings, the new `REFACTOR_resource_hub.md` snippets.
- Docs prose, `mkdocs.yml` `site_name`, examples' import lines.

The Rust side is already `operonx` — no change there.

### Renaming the GitHub repo

GitHub supports renaming a repository in-place: **Settings → General → Repository name → Rename**. After the rename:

- **Old URLs auto-redirect.** `github.com/batman1m2001-cyber/Operon` keeps working forever, redirecting to the new URL. Anyone who cloned with the old name can still `git push` / `git pull`; GitHub handles the redirect transparently.
- **Locally**, run `git remote set-url origin https://github.com/batman1m2001-cyber/Operonx.git` to point at the new canonical URL. Optional but tidier.
- **CI badges, README links, docs site** that hard-code the URL need updating in this same refactor (covered as part of the "rewrite README" + "mkdocs.yml" steps below).
- **PyPI / crates.io publishing** is unaffected — they reference the package name and CI tokens, not the repo URL.

Cost is essentially zero. Do it as part of Step 0 of the migration so the rest of the plan can reference the new URL throughout.

## Goals

| Goal | How |
|---|---|
| CI/CD works on the actual repo layout | Rewrite all 7 workflows for the single-package + single-crate shape. |
| `pip install operonx[anthropic]` etc. is verified per release | Add an `extras` matrix job that installs each extra and runs an import smoke test. |
| Public API docs auto-generate from docstrings | Add `mkdocs.yml` + `docs/api/` with mkdocstrings, build on PR, deploy on push to `dev`. |
| Architecture and guide docs land | Port `docs/architecture/*.md` from Hush, update Hush→Operonx naming. |
| Contributors run lint + format on commit | Add `.pre-commit-config.yaml` with ruff. |
| Coverage tracked with PR comments | Wire `codecov.yml` + badge in README. |
| Repo presents well at first glance | Rewrite README, add CHANGELOG, add badges. |

## A. Architecture docs + auto-build

### Layout (mirrors Hush, scoped to Operonx)

```
docs/
├── index.md              # Landing — "what is Operonx", quick start
├── architecture/
│   ├── overview.md       # 1-page system diagram + glossary
│   ├── execution-flow.md # Engine + scheduler walkthrough
│   ├── state-model.md    # PARENT vs op[key], schema, frames
│   ├── streaming.md      # Generator ops, handle, frame stream
│   ├── rust-python.md    # When to pick which backend, parity guarantees
│   └── resource-hub.md   # bootstrap(), auto(), full failure model (self-contained — REFACTOR_resource_hub.md will be deleted)
├── guide/
│   ├── 00-installation.md
│   ├── 01-first-workflow.md
│   ├── 02-llm-chat.md
│   ├── 03-loops-and-branches.md
│   ├── 04-rag.md
│   ├── 05-agents.md
│   ├── 06-streaming.md
│   ├── 07-tracing.md
│   └── 08-deployment.md
└── api/                  # All mkdocstrings auto-rendered
    ├── core.md           # operonx.core
    ├── ops.md            # operonx.core.ops
    ├── providers.md      # operonx.providers
    ├── telemetry.md      # operonx.telemetry
    └── registry.md       # operonx.core.registry (ResourceHub, bootstrap)
```

### `mkdocs.yml`

- `site_name: Operonx`, `repo_url: https://github.com/batman1m2001-cyber/Operonx`.
- Theme: `material` (matches Hush — same dark/light toggle, deep purple primary).
- Plugins: `search`, `mkdocstrings[python]` with `paths: [.]` and `options: docstring_style: google`, `merge_init_into_class: true`, `show_root_heading: true`, `show_signature_annotations: true`.
- Nav structure flat enough that `mkdocs build --strict` passes (no broken links, no orphan pages).

### Docstring policy

- **Public API surface only.** Public = anything in `__all__` of `operonx/__init__.py` or any submodule's `__all__`. Internal helpers stay undocumented (mkdocstrings ignores them via `filters: ["!^_"]`).
- **Google style** to match what mkdocstrings renders cleanest: `Args:`, `Returns:`, `Raises:`, `Example:`.
- **No new prose docstrings unless the existing one is empty or wrong.** Most public ops/classes already have decent docstrings from the migration; this refactor lints the rendered output, not the prose quality.

### Auto-build CI (`docs.yaml` rewrite)

```yaml
on:
  push: { branches: [dev], paths: [docs/**, operonx/**, mkdocs.yml] }
  pull_request: { branches: [dev], paths: [docs/**, operonx/**, mkdocs.yml] }
jobs:
  build:
    - uv sync --all-extras   # one package, one sync — no per-package loop
    - uv run mkdocs build --strict
    - upload site/ as Pages artifact (only on push to dev)
  deploy:
    - if: push to dev — actions/deploy-pages@v4
```

Strict mode catches broken cross-references and missing imports at PR time.

## B. CI/CD — fix all 7 workflows

Every workflow currently references the dead Hush layout. Each gets a targeted rewrite for the single-package + single-crate shape.

### `tests.yaml`

- **Python:** drop the per-package matrix; run `uv sync --all-extras && uv run pytest tests/ -m "not integration"` once at repo root. Add an `extras-smoke` matrix job that installs `operonx[anthropic]`, `operonx[langfuse]`, `operonx[otel]`, `operonx[onnx]`, `operonx[serve]`, `operonx[all]` and runs `python -c "import operonx; ..."` to catch packaging breakage.
- **Examples:** `uv run python -m examples.python.ex01_hello_world.demo --runs 1` and `ex02_data_pipeline` (both pure-compute, no API keys).
- **Coverage:** `uv run pytest tests/ --cov=operonx --cov-report=xml -m "not integration"` then upload to codecov. Drop the per-package coverage step.
- **Docs job stays in `tests.yaml` for PRs, but only does `mkdocs build --strict` (build, not deploy)** — fast feedback per PR. Push-to-dev deploy stays in `docs.yaml`.

### `publish.yaml`

- **Python publish:** drop the 5-package matrix. Single job: detect version bump in root `pyproject.toml`, build with `uv build`, publish to PyPI. The token name stays `PYPI_API_TOKEN`.
- **Rust publish:** drop the 5-crate sequential publish. Two crates: `operonx-macros` first (no deps), then `operonx` (depends on macros). Use existing `CARGO_REGISTRY_TOKEN`.
- **Trigger:** push to `dev` (matches current default branch). Stay event-driven on version-string change.
- **Tag the release:** create a Git tag `v<version>` after a successful publish so users can `pip install operonx==X.Y.Z` and reach back to the source.

### `format.yaml`

- Drop multi-package loops. Single command: `uv run ruff check operonx/ tests/ examples/python/` and `uv run ruff format --check operonx/ tests/ examples/python/`.
- Same gate on PRs.

### `python-compatibility.yaml`

- Matrix `[3.10, 3.11, 3.12]`. Run `uv sync --all-extras && uv run pytest tests/ -m "not integration"` per Python version.
- Single package — no per-package loop.

### `rust-runtime.yaml`

- `cd rust && cargo test --workspace` and `cargo build --workspace --release`. That's it. The current workflow is roughly correct but mentions stale crate names in cache keys.

### `parity.yaml`

- Already has the right shape (compares Python and Rust outputs across `tests/spec/`). Just verify it runs against the new test layout (`tests/spec/test_fixtures.py`). Likely a 5-line update.

### Deletion

- None. All 7 workflows survive the refactor — they each map to a real CI need.

## C. PyPI extras strategy

Current extras in `pyproject.toml`:

| Extra | Contents | Status |
|---|---|---|
| `providers` | openai, aiohttp, numpy | OK (think of as "core providers") |
| `anthropic` | anthropic | OK |
| `gemini` | google-cloud-aiplatform, requests | OK |
| `bedrock` | boto3 | OK |
| `onnx` | onnxruntime, tokenizers | OK |
| `huggingface` | transformers, torch | OK (heavy — torch ≈ 2.5 GB) |
| `langfuse` | langfuse | OK |
| `otel` | opentelemetry-* | OK |
| `serve` | fastapi, uvicorn, websockets | OK |
| `all` | providers + langfuse + otel + serve | **Doesn't include `anthropic`, `gemini`, `bedrock`, `onnx`, `huggingface`** — bug. |
| `dev` | pytest, ruff, ... | OK |

### Changes

1. **Fix `all`** to actually include the LLM provider extras: `all = ["operonx[providers,anthropic,gemini,bedrock,onnx,langfuse,otel,serve]"]`. Drop `huggingface` from `all` because torch is too heavy for a default-fat install. Document this.
2. **Add `standard`** as the lighter "production-ready, no exotic deps" extra: `standard = ["operonx[providers,langfuse,otel,serve]"]`. This is what most users will want — same as today's `all` but without `anthropic`/`gemini`/`bedrock`/`onnx`/`huggingface`.
3. **CI extras-smoke matrix** — install each extra in isolation, run `python -c "import operonx"` plus a one-line operation specific to that extra (e.g. `operonx[anthropic]` does `from operonx.providers.llms.anthropic import AnthropicLLM` to verify the optional import path works). Catches missing dep declarations before users hit them.

## D. crates.io publish

- Two crates: `operonx-macros` (no deps), `operonx` (depends on macros).
- Order: `cargo publish -p operonx-macros && sleep 30 && cargo publish -p operonx`.
- Both crates need to share the same version number (bumped together in `Cargo.toml` per crate).
- **Open question 2:** should `operonx-macros` be a public crate at all, or `publish = false`? It's only useful to operonx users via the re-export. Recommendation: publish it (so `operonx` can declare it as a dep), but mark it with `# Internal — re-exported via operonx::{op, model, resource}` in the crate description so people don't depend on it directly.

## E. Pre-commit

Add `.pre-commit-config.yaml` mirroring Hush, scoped to Operonx's actual file layout:

```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.15.2
    hooks:
      - id: ruff
        args: [--fix]
        files: ^(operonx|tests|examples/python)/
      - id: ruff-format
        files: ^(operonx|tests|examples/python)/
  - repo: https://github.com/doublify/pre-commit-rust
    rev: v1.0
    hooks:
      - id: fmt
        args: [--manifest-path=rust/Cargo.toml, --]
      - id: clippy
        args: [--manifest-path=rust/Cargo.toml, --workspace, --, -D, warnings]
```

Document install in CONTRIBUTING.md: `pre-commit install` once after clone. Add `pre-commit run --all-files` as a CI gate in `format.yaml` so unformatted PRs fail before review.

**Recommendation: yes, add it.** The cost is one config file plus one line in CONTRIBUTING. The benefit is every PR is auto-formatted and clippy-clean. Worth it for a public repo.

## F. Codecov

`tests.yaml` already references `codecov/codecov-action@v4` and `CODECOV_TOKEN`. Two missing pieces:

1. **`codecov.yml`** at repo root — set thresholds (e.g. `target: 80%`, `threshold: 1%` so a 1-percentage-point drop fails). Configure `comment.layout` to post a per-PR coverage diff.
2. **README badge** — `[![codecov](https://codecov.io/gh/batman1m2001-cyber/Operonx/branch/dev/graph/badge.svg)](https://codecov.io/gh/batman1m2001-cyber/Operonx)`.

**Recommendation: yes, add it.** Codecov already partially wired; finishing the wiring is 2 small files. The PR comment is genuinely useful for spotting untested code paths during review.

Coverage scope: `operonx/` only. Don't measure `tests/`, `examples/`, or `rust/` (Rust would need `cargo-llvm-cov` and a separate Codecov upload — out of scope for now, can add later as a second job if you want it).

## G. Repo readiness checklist

Beyond docs/CI/extras, a public repo needs:

| Item | Status | Action |
|---|---|---|
| README.md | Out of date — "Hush" everywhere | **Rewrite.** Quick Start + LLM Integration with `operonx.bootstrap()`, package list (PyPI + crates.io), badges, links to docs site. |
| CHANGELOG.md | Missing | **Add.** Seed with a 0.6.0 entry summarizing the migration + resource-hub refactor. Use [keep-a-changelog.com](https://keepachangelog.com) format. |
| LICENSE | Apache-2.0, present | OK |
| SECURITY.md | Present | Review — likely outdated content (probably says "Hush"). |
| CONTRIBUTING.md | Present | Review — add pre-commit install step + uv-based dev setup. |
| CODE_OF_CONDUCT.md | Missing | Add Contributor Covenant 2.1 boilerplate. Five-minute job. |
| Issue templates | Unknown — check `.github/ISSUE_TEMPLATE/` | Add bug-report + feature-request templates if missing. |
| PR template | Unknown | Add `.github/PULL_REQUEST_TEMPLATE.md` with checklist (tests, docs, CHANGELOG entry). |
| Repo metadata (description, topics) | GitHub-side, not in code | Document in CONTRIBUTING what topics to set. |
| `env.example` | Present | Verify it lists every env var Operonx resolves; add Operonx-specific names. |
| `MIGRATION_*.md` files | Scratch notes from the Hush→Operonx migration | **Delete.** Their content is captured in this repo's history; they're not user-facing docs. |
| `REFACTOR_*.md` files | Scratch plans (this one + resource_hub) | **Delete.** Same — temporary planning artifacts. Anything load-bearing for users moves into `docs/architecture/`. |

## File-by-file changes

| File | New / Modified / Deleted | What |
|---|---|---|
| `operonx/` → `operonx/` | Renamed | `git mv` the source tree. |
| `pyproject.toml` | Modified | `name = "operonx"`; fix `[all]` self-references to `operonx[...]`; add `[standard]` extra. |
| `operonx/` (every `.py`) | Modified | `import operonx` / `from operonx.X` → `operonx`. Mostly via ripgrep + sed. |
| `tests/` (every `.py`) | Modified | Same import rewrite. |
| `examples/python/` (every `.py`) | Modified | Same import rewrite. |
| Public-facing strings | Modified | Error messages naming `operonx.bootstrap()` → `operonx.bootstrap()`; docstrings; CLAUDE.md. |
| `mkdocs.yml` | New | Material theme, mkdocstrings, full nav structure. |
| `docs/index.md` | New | Landing. |
| `docs/architecture/*.md` | New (6 files) | Port from Hush, update naming. The `resource-hub.md` page absorbs whatever's load-bearing from `REFACTOR_resource_hub.md` so the latter can be deleted. |
| `docs/guide/*.md` | New (8 files) | Port from Hush, update naming. |
| `docs/api/*.md` | New (5 files) | mkdocstrings stubs (one-liners that pull from `::: operonx.core` etc.). |
| `REFACTOR_*.md`, `MIGRATION_*.md` | Deleted | All scratch planning files removed once their content is either obsolete or absorbed into `docs/`. |
| `.github/workflows/tests.yaml` | Rewrite | Single-package layout + extras-smoke matrix. |
| `.github/workflows/docs.yaml` | Rewrite | Single-package mkdocs build + deploy on push to dev. |
| `.github/workflows/publish.yaml` | Rewrite | Single PyPI package (`operonx`) + 2 Rust crates (`operonx-macros`, `operonx`). Tag releases. |
| `.github/workflows/format.yaml` | Rewrite | Single ruff invocation. |
| `.github/workflows/python-compatibility.yaml` | Rewrite | 3.10/3.11/3.12 matrix on single package. |
| `.github/workflows/rust-runtime.yaml` | Update | Cache keys, no per-crate paths. |
| `.github/workflows/parity.yaml` | Verify | Likely 5-line tweak. |
| `.pre-commit-config.yaml` | New | Ruff + cargo fmt + clippy. |
| `codecov.yml` | New | Threshold 80%, PR comment layout. |
| `README.md` | Rewrite | Operonx naming, `operonx.bootstrap()` quick start, badges, package table. |
| `CHANGELOG.md` | New | Seed with 0.6.0 entry. |
| `CODE_OF_CONDUCT.md` | New | Contributor Covenant 2.1. |
| `.github/ISSUE_TEMPLATE/bug.yml`, `feature.yml` | New if missing | GitHub issue forms. |
| `.github/PULL_REQUEST_TEMPLATE.md` | New if missing | PR checklist. |
| `CONTRIBUTING.md` | Modified | Add `pre-commit install` + `uv sync --all-extras` setup steps. |
| `SECURITY.md` | Verify | Review for stale Hush references. |
| `env.example` | Verify | Audit env vars. |

## Migration steps (suggested order)

0. **Rename `operonx` → `operonx` (code) and rename the GitHub repo.**
   - `git mv operonx operonx`. Ripgrep-and-sed every `import operonx` / `from operonx.` / `operonx[` / `operonx.bootstrap()`. Update `pyproject.toml` `name`.
   - Run `uv run pytest tests/` — must stay green. Run both `ex01_hello_world` examples (Python + Rust).
   - Rename the GitHub repo via Settings → General → Repository name. Update `git remote set-url origin` locally. URL redirects keep old links working.
   - Single commit (or two: one for the code rename, one for the URL bumps in README/docs/workflows that reference the repo URL).
   - **Do this first** because every later step references the new name.
1. **Fix CI workflows.** Rewrite all 7 workflows for the single-package + single-crate shape, using the new `operonx` name. Get to a green pipeline on `dev` before adding new surface area. One PR per workflow for easy rollback, or one big PR if you trust the rewrite.
2. **Add docs scaffolding.** `mkdocs.yml` + `docs/api/*.md` stubs + `docs/index.md`. Verify `mkdocs serve` renders locally and `mkdocs build --strict` passes in CI.
3. **Port architecture + guide content.** Mechanical rename of Hush→Operonx in the prose, code blocks updated to use `operonx.bootstrap()` + new APIs. PR-by-PR or one big PR — your call. The `docs/architecture/resource-hub.md` page absorbs whatever's load-bearing from `REFACTOR_resource_hub.md` so the latter can be deleted in step 6.
4. **README rewrite + CHANGELOG + CODE_OF_CONDUCT.** Lightweight, parallelizable.
5. **Pre-commit + codecov configs.** Two small files, one CONTRIBUTING update.
6. **Delete scratch planning files.** `rm REFACTOR_*.md MIGRATION_*.md` once docs/ owns the surviving content. These were always temporary — git history preserves them if anyone ever needs to look back.
7. **Tag a release.** First clean release (v0.6.1 — patch bump for the readiness work) is the proof point. After this, `pip install operonx[standard]` should Just Work for a stranger.

Each step is independently revertable. I'd batch (0)-(1) as one PR, (2)-(3) as one PR, (4)-(7) as one PR.

## Open questions

1. ~~Naming — `operonx` vs `operonx`?~~ **Resolved: rename to `operonx`.** PyPI's `operonx` is taken by an unrelated project, the package was never published, so the cost is purely mechanical. See [Naming decision](#naming-decision-resolved--operonx-everywhere).
2. **Publish `operonx-macros` to crates.io, or mark `publish = false`?** Recommendation: publish, with a description that flags it as internal-but-required (consumed via `operonx::{op, model, resource}` re-exports).
3. **Pre-commit hooks for Rust — clippy as warnings or errors?** Recommendation: errors (`-D warnings`). Local pain prevents CI pain.
4. **Codecov target — 80%, or just track without enforcing?** Recommendation: set `target: 80%` but `informational: true` for the first month so it doesn't block PRs while the test suite stabilizes. Flip to enforcing later.
5. **Should the docs site live at `batman1m2001-cyber.github.io/Operonx` (default GitHub Pages) or a custom domain?** Recommendation: default GH Pages for now; custom domain is a 5-minute switch later if you want one.
6. **Vietnamese guide chapters from Hush — port verbatim, or skip?** Hush's `docs/guide/` was 14 chapters in Vietnamese. They're solid pedagogy. Recommendation: port them, update naming. Operonx's Vietnamese audience benefits, and English-only contributors can ignore the guide section. Out-of-scope: translating to English (separate effort, separate PR).

## Acceptance criteria

- All 7 GitHub workflows pass on `dev`.
- A fresh clone with `uv sync --all-extras && uv run pytest tests/` returns green.
- `pip install operonx[anthropic]` (or any single extra) works in a fresh venv. CI has a test for this per extra.
- `mkdocs build --strict` passes; the docs site builds without warnings.
- The published docs site renders the API reference for every public class and function in `operonx.core`, `operonx.providers`, `operonx.telemetry`, `operonx.core.registry`.
- `pre-commit install && pre-commit run --all-files` returns green on a fresh clone.
- Codecov badge in README shows real coverage; PR comments post a coverage diff.
- README quickstart works end-to-end when copy-pasted by a user who doesn't have the repo cloned.
- A stranger can `pip install operonx[standard]` and the LLM Integration example from the README runs.
- No `REFACTOR_*.md` or `MIGRATION_*.md` files remain in the repo root — everything user-facing lives in `docs/`.
