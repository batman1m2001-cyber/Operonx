# Contributing to Operonx

Thank you for your interest in contributing to Operonx! This guide gets you set up and shipping.

> Working on the Rust runtime? See
> [operonx-rs](https://github.com/batman1m2001-cyber/operonx-rs). This repo
> hosts only the Python side.

## Development Setup

### Prerequisites

- Python 3.10+
- [uv](https://docs.astral.sh/uv/) package manager

### Clone and Install

```bash
git clone https://github.com/batman1m2001-cyber/Operonx.git
cd Operonx
uv sync --all-extras
```

This installs `operonx` and every optional extra (`anthropic`, `langfuse`, `otel`, `serve`, ...) plus the dev toolchain.

### Pre-commit Hooks

Pre-commit runs ruff on every commit:

```bash
uv tool install pre-commit
pre-commit install
pre-commit run --all-files   # one-off check across the tree
```

### Environment

Most provider tests are auto-skipped without `resources.yaml` + `.env`. To run them:

```bash
cp env.example .env
# Edit .env with your API keys
```

## Code Style

- **Formatter**: Ruff (Black-compatible)
- **Line length**: 100 characters
- **Linter**: Ruff with rules `E, F, I, W` (see `pyproject.toml` for the full ignore list)
- **Type hints**: Required on public APIs; Pydantic for config
- **Docstrings**: Google style on public surface (`__all__` entries)

```bash
uv run ruff format operonx/ tests/ examples/python/
uv run ruff check --fix operonx/ tests/ examples/python/
```

## Testing

```bash
# Unit + non-integration tests (default — what CI runs)
uv run pytest tests/ -m "not integration"

# Full suite including integration tests (needs API keys)
uv run pytest tests/

# With coverage
uv run pytest tests/ --cov=operonx --cov-report=term-missing -m "not integration"
```

Tests under `tests/internal/providers/` are auto-marked `integration` (they hit real APIs or need a configured `ResourceHub`); they're excluded by default and only run when API credentials are present.

## Pull Request Workflow

1. **Fork** and clone
2. **Branch** from `dev`:
   ```bash
   git checkout -b feature/your-feature-name dev
   ```
3. **Code + test** — keep PRs focused; one logical change per PR
4. **Verify locally**:
   - `pre-commit run --all-files`
   - `uv run pytest tests/ -m "not integration"`
5. **Open PR** against `dev`. CI runs the full matrix; review starts when it's green.

### PR Checklist

- [ ] Tests pass locally
- [ ] `pre-commit` clean
- [ ] Public API changes have docstrings
- [ ] CHANGELOG entry added under `## [Unreleased]` (for user-visible changes)
- [ ] Documentation updated if touching architecture or public APIs

## Branch Policy

- `main` — release branch. Only updated via PR from `dev`. CI/CD publishes on version bumps here.
- `dev` — integration branch. PRs land here. Default branch for new work.

## Project Structure

```
Operonx/
├── operonx/              # Python package (single package, optional extras)
│   ├── core/             # Engine, ops, state, registry, telemetry hooks
│   ├── providers/        # LLM, embedding, reranking, auth integrations
│   └── telemetry/        # Consumers: local, Langfuse, OTEL
├── examples/python/      # Runnable examples
├── tests/
│   ├── internal/         # Engine + provider tests
│   └── spec/             # JSON-fixture tests (mirrored in operonx-rs)
├── docs/
│   ├── architecture/     # Internals + design docs
│   ├── guide/            # User guide
│   └── api/              # Auto-generated API reference (mkdocstrings)
└── .github/workflows/    # CI/CD
```

## Cross-repo work

The `tests/spec/` JSON fixtures are duplicated in
[operonx-rs/tests/spec/](https://github.com/batman1m2001-cyber/operonx-rs/tree/main/tests/spec).
When adding or changing a fixture, land the same change in both repos.

## Documentation

| Change Type | Update |
|-------------|--------|
| New op type | `operonx/core/ops/` + relevant guide chapter |
| New provider | `operonx/providers/` + `docs/architecture/` if non-trivial |
| New consumer | `operonx/telemetry/consumers/` |
| Public API change | Update docstring + CHANGELOG; mkdocstrings re-renders the API page |
| Architectural change | Add/update a page in `docs/architecture/` |

## Questions

- [Open a question issue](https://github.com/batman1m2001-cyber/Operonx/issues/new?template=3-question.yml)
- Browse [docs/guide/](docs/guide/) for the user guide

## License

By contributing, you agree your contributions will be licensed under the Apache 2.0 License.
