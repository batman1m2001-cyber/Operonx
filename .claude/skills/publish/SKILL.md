---
name: publish
description: Bump the Python package version, commit, PR, and publish to PyPI
---

# /publish — Version Bump & Release

Bump the Python package, run tests, commit, tag, and publish to PyPI.

> Rust crate publishing lives in the sibling repo
> [operonx-rs](https://github.com/batman1m2001-cyber/operonx-rs) and has
> its own `/publish` flow.

## Steps

### 1. Determine new version

Ask the user for the new version (e.g., `0.9.1`). If they just say "bump",
increment the patch version.

### 2. Bump Python package

Update `version = "X.Y.Z"` in:
- [pyproject.toml](pyproject.toml) (top-level `[project]` table)
- [operonx/__init__.py](operonx/__init__.py) (`__version__`)

### 3. Run tests

```bash
uv sync --all-extras
uv run pytest tests/ -m "not integration"
```

### 4. Update CHANGELOG

Move the `## [Unreleased]` content under a new `## [X.Y.Z] - YYYY-MM-DD`
heading. Update the comparison links at the bottom.

### 5. Commit, tag, and PR

```bash
git add pyproject.toml operonx/__init__.py CHANGELOG.md
git commit -m "chore: bump to vX.Y.Z"
```

Open a PR against `main`:

```bash
gh pr create --base main --title "chore: bump to vX.Y.Z" --body "..."
```

### 6. After merge

The Publish workflow ([.github/workflows/publish.yaml](.github/workflows/publish.yaml))
auto-triggers on version-string change in `main`:
- Publishes the Python package to PyPI (via `PYPI_API_TOKEN`).
- Creates a `vX.Y.Z` git tag.

### 7. Verify

```bash
pip index versions operonx
```

## Git identity

Always commit as `Bruce Win <batman1m2001@gmail.com>`. Never add
Co-Authored-By lines.
