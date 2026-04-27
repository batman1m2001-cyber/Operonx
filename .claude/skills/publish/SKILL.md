---
name: publish
description: Bump version across the Python package + Rust crates, commit, PR, and publish to PyPI + crates.io
---

# /publish — Version Bump & Release

Bump the single Python package + the two Rust crates, run tests, commit, tag,
and publish.

## Steps

### 1. Determine new version

Ask the user for the new version (e.g., `0.6.2`). If they just say "bump",
increment the patch version.

### 2. Bump Python package

Update `version = "X.Y.Z"` in:
- [pyproject.toml](pyproject.toml) (top-level `[project]` table)
- [operonx/__init__.py](operonx/__init__.py) (`__version__`)

### 3. Bump Rust crates

The Rust workspace shares one version. Update `version = "X.Y.Z"` in:
- [rust/Cargo.toml](rust/Cargo.toml) (`[workspace.package]` — both `operonx`
  and `operonx-macros` inherit from here via `version.workspace = true`)

If `operonx` declares an explicit dependency version on `operonx-macros`,
also bump that pin.

### 4. Run tests

```bash
# Python — single command
uv sync --all-extras
uv run pytest tests/ -m "not integration"

# Rust — single workspace
cd rust && cargo test --workspace
```

### 5. Update CHANGELOG

Move the `## [Unreleased]` content under a new `## [X.Y.Z] - YYYY-MM-DD`
heading. Update the comparison links at the bottom.

### 6. Commit, tag, and PR

```bash
git add pyproject.toml operonx/__init__.py rust/Cargo.toml CHANGELOG.md
git commit -m "chore: bump to vX.Y.Z"
git tag vX.Y.Z
git push origin main
git push origin vX.Y.Z
```

If working off a feature branch, open a PR first:

```bash
gh pr create --base main --title "chore: bump to vX.Y.Z" --body "..."
```

### 7. After merge / push

The Publish workflow ([.github/workflows/publish.yaml](.github/workflows/publish.yaml))
auto-triggers on version-string change in `main`:
- Publishes the Python package to PyPI (via `PYPI_API_TOKEN`)
- Publishes the Rust crates to crates.io (via `CARGO_REGISTRY_TOKEN`)

crates.io publish order (dependencies):

```
operonx-macros  (no operonx deps)
operonx         (depends on operonx-macros)
```

`cargo publish -p operonx-macros && sleep 30 && cargo publish -p operonx`.
The sleep gives crates.io time to index the macros crate before `operonx` resolves.

### 8. Verify

```bash
pip index versions operonx
cargo search operonx
```

## Git identity

Always commit as `Bruce Win <batman1m2001@gmail.com>`. Never add
Co-Authored-By lines.
