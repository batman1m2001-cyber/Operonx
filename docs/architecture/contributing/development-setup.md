# Development Setup

## Prerequisites

- Python >= 3.10
- [uv](https://github.com/astral-sh/uv) (package manager khuyến nghị)
- Git

## Project Structure

```
hush/
├── hush-core/          # Core workflow engine
├── hush-providers/     # LLM, embedding, reranker providers
├── hush-telemetry/ # Tracing integration (Langfuse, etc.)
├── tutorial/      # Tutorials và examples
├── architecture/       # Internal documentation
└── tutorial/docs/ # User documentation
```

## Clone Repository

```bash
git clone https://github.com/batman1m2001-cyber/Hush-ai.git
cd hush
```

## Setup với uv (Recommended)

### hush-core

```bash
cd hush-core
uv venv
source .venv/bin/activate  # Linux/macOS
.venv\Scripts\activate     # Windows

uv pip install -e ".[dev]"
```

### hush-providers

```bash
cd hush-providers
uv venv
source .venv/bin/activate

# Basic install
uv pip install -e ".[dev]"

# With optional dependencies
uv pip install -e ".[dev,gemini,huggingface]"

# All dependencies
uv pip install -e ".[dev,all]"
```

### hush-telemetry

```bash
cd hush-telemetry
uv venv
source .venv/bin/activate
uv pip install -e ".[dev]"
```

## Development Mode với Editable Links

uv sources được cấu hình trong `pyproject.toml` để link packages:

```toml
# hush-providers/pyproject.toml
[tool.uv.sources]
hush-core = { path = "../hush-core", editable = true }

# hush-core/pyproject.toml
[tool.uv.sources]
hush-telemetry = { path = "../hush-telemetry", editable = true }
```

Khi develop, thay đổi ở một package sẽ reflect ngay ở packages khác.

## IDE Setup

### VSCode

Recommended extensions:
- Python (ms-python.python)
- Pylance (ms-python.vscode-pylance)
- Ruff (charliermarsh.ruff)

Settings:

```json
{
    "python.defaultInterpreterPath": ".venv/bin/python",
    "editor.formatOnSave": true,
    "[python]": {
        "editor.defaultFormatter": "charliermarsh.ruff"
    }
}
```

### PyCharm

1. Mark `hush-core/hush`, `hush-providers/hush` as Sources Root
2. Configure Python interpreter từ `.venv`
3. Enable Black/Ruff formatter

## Environment Variables

### Development

```bash
# .env
HUSH_TRACES_DB=~/.hush/traces.db
HUSH_CONFIG_PATH=./configs
```

### Testing với External Services

```bash
# .env.test
OPENAI_API_KEY=sk-test-...
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
```

## Building

### Build Packages

```bash
# Build hush-core
cd hush-core
uv build

# Build hush-providers
cd hush-providers
uv build
```

### Local Install

```bash
pip install dist/hush_core-0.1.0-py3-none-any.whl
```

## Common Issues

### Import Errors

Đảm bảo install với editable mode:

```bash
pip install -e .
```

### Module Not Found

Check Python path:

```python
import sys
print(sys.path)
```

### Event Loop Errors (Windows)

Code đã handle automatic:

```python
# hush-providers/hush/providers/llms/base.py
if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
```

## Quick Test

```bash
# Verify installation
python -c "from hush.core import GraphOp, FuncOp; print('OK')"

# Run tests
pytest tests/ -v
```
