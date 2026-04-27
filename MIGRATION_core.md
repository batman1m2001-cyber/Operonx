# Migration Plan: `hush-icore` → `operonx/core`

## Project layout (target)

Project root: `C:\Users\Dell\Desktop\Work\Operon\operonx\` (referred to as `/` below).

```
/                           ← pyproject.toml lives here
├── pyproject.toml
├── operonx/                 ← the Python package
│   └── core/               ← contents from hush-icore/hush/core/
│       ├── __init__.py
│       ├── engine.py
│       ├── ops/
│       ├── states/
│       ├── tracing/
│       ├── registry/
│       ├── middleware/
│       ├── configs/
│       ├── utils/
│       ├── loggings.py
│       └── exceptions.py
└── tests/
    └── core/               ← contents from hush-icore/tests/
        ├── conftest.py
        └── test_*.py
```

Import path: `from operonx.core import Operon, GraphOp, ...`

## Decisions locked in

| # | Decision |
|---|----------|
| 1 | Engine class `Hush` → `Operon` |
| 2 | Tests at `/tests/core/` (one unified dir at root) |
| 3 | Skip `examples/` and internal docs for now — deferred |

## Rename rules (applied globally)

| From | To |
|------|-----|
| `from hush.core` | `from operonx.core` |
| `from hush ` | `from operonx ` |
| `import hush` | `import operonx` |
| `Hush` class | `Operon` class |
| `hush-icore` package refs | drop / replace with `operonx` |
| Docstring mentions of "Hush" | "Operon" |
| `hush-ai` / `hush/` folder names | `operonx/` |

## Execution steps

1. **Inventory source** — list all files under `Hush-ai/python/hush-icore/hush/core/` and `Hush-ai/python/hush-icore/tests/`.
2. **Copy source tree** — copy `hush/core/*` → `/operonx/core/*` verbatim.
3. **Copy tests tree** — copy `tests/*` → `/tests/core/*` verbatim.
4. **Global rename pass** — apply the rename rules table across all copied files.
5. **Engine class rename** — `class Hush:` → `class Operon:` in `engine.py`; update `__init__.py` exports; update every `Hush(...)` callsite in tests.
6. **Create/update root `pyproject.toml`** — merge `hush-icore`'s dependencies (`pydantic>=2.0`, `pyyaml>=6.0.3`, `rich>=13.0`, `orjson>=3.9`, `python-dotenv`) + dev deps (`pytest`, `pytest-asyncio`, `ruff`). Package name `operonx`, version seeded from Operon.
7. **Drop hush-specific artifacts** — `pyproject.toml` from hush-icore (not copied), any `CLAUDE.md`, package-level README stubs that only describe the old hush split.
8. **Smoke test** — `cd /` → `uv pip install -e ".[dev]"` → `uv run -m pytest tests/core/ -q`.
   - Expected: **707 passed, 1 skipped** (matches current hush-icore baseline).

## Out of scope for this pass

- Examples (`examples/ex01–ex15`)
- Docs (`docs/architecture/*`, guide chapters)
- Rust backend
- Provider ops, telemetry, serve (separate passes)
- CLI / `operonx new` scaffolding

## Risks / things to watch

- **`hush.core` sub-imports** — some files use relative imports (`from .engine import ...`) which survive as-is; absolute imports (`from hush.core.states import ...`) need rewriting.
- **`__slots__` with string keys** — fine, no change needed.
- **Test fixtures referencing `Hush`** — covered by rename rule 5.
- **`engine.py` docstrings mention "Hush engine"** — cosmetic but should be caught by rename rule 6.

## Success criteria

- `pytest tests/core/` → 707 passed, 1 skipped
- `python -c "from operonx.core import Operon, GraphOp, START, END, PARENT; print('ok')"` succeeds
- No references to `hush` left in `/operonx/core/` or `/tests/core/` (verified by grep)
