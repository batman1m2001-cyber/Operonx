# Contributing to Hush

Thank you for your interest in contributing to Hush! This guide will help you get started.

## Development Setup

### Prerequisites

- Python 3.10+
- [uv](https://docs.astral.sh/uv/) package manager

### Clone and Install

```bash
git clone https://github.com/batman1m2001-cyber/Hush-ai.git
cd Hush-ai

# Install hush-core (foundation)
cd hush-core && uv sync --all-extras && cd ..

# Install hush-providers (depends on hush-core)
cd hush-providers && uv sync --all-extras && cd ..

# Install hush-observability (depends on hush-core)
cd hush-observability && uv sync --all-extras && cd ..
```

### Pre-commit Hooks

We use pre-commit to ensure code quality:

```bash
# Install pre-commit
uv tool install pre-commit

# Set up hooks
pre-commit install

# Run on all files (optional)
pre-commit run --all-files
```

## Code Style

- **Formatter**: Black-compatible via Ruff
- **Line length**: 100 characters
- **Linter**: Ruff with rules `E, F, I, W`
- **Type hints**: Use typing module, Pydantic for validation
- **Docstrings**: Google style (optional, only where helpful)

### Running Checks

```bash
# Format code
ruff format .

# Lint and auto-fix
ruff check --fix .
```

## Testing

We use pytest with pytest-asyncio:

```bash
# Run tests for a package
cd hush-core && uv run pytest

# Run with coverage
cd hush-core && uv run pytest --cov=hush
```

## Pull Request Workflow

1. **Fork** the repository
2. **Create a branch** from `main`:
   ```bash
   git checkout -b feature/your-feature-name
   ```
3. **Make changes** following our code style
4. **Write tests** for new functionality
5. **Run tests** locally to ensure they pass
6. **Commit** with a clear message
7. **Push** and open a Pull Request

### PR Checklist

- [ ] Tests pass locally
- [ ] Code is formatted (`ruff format`)
- [ ] Linter passes (`ruff check`)
- [ ] Documentation updated if needed

## Documentation Updates

When making changes, update the appropriate documentation:

| Change Type | Update |
|-------------|--------|
| New node type | `hush-core/CLAUDE.md`, `hush-tutorial/docs/03-core-concepts.md` |
| New provider | `hush-providers/CLAUDE.md`, `hush-tutorial/docs/04-llm-integration.md` |
| New tracer | `hush-observability/CLAUDE.md`, `hush-tutorial/docs/09-tracing-observability.md` |
| API change | Relevant `CLAUDE.md` + tutorial docs |
| Internal refactor | `architecture/` if algorithm changes |

See [CLAUDE.md](CLAUDE.md) for the complete sync mapping.

## Project Structure

```
Hush-ai/
├── hush-core/          # Core workflow engine
├── hush-providers/     # LLM, embedding, reranking
├── hush-observability/ # Tracing backends
├── hush-tutorial/      # Docs (Vietnamese) + examples
├── hush-vscode-traceview/ # VS Code extension
├── architecture/       # Deep technical docs
└── CLAUDE.md           # Quick reference
```

## Package Dependencies

```
hush-core (foundation - no hush dependencies)
    ↓
hush-providers (depends on hush-core)
    ↓
hush-observability (depends on hush-core)
```

## Questions?

- Open a [Question issue](https://github.com/batman1m2001-cyber/Hush-ai/issues/new?template=3-question.yml)
- Check existing documentation in `hush-tutorial/docs/`

## License

By contributing, you agree that your contributions will be licensed under the Apache 2.0 License.
