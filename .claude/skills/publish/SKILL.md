---
name: publish
description: Bump versions across all Python + Rust packages, commit, PR, and publish to PyPI + crates.io
---

# /publish — Version Bump & Release

Bump all package versions, run tests, commit, create PR, and publish.

## Steps

### 1. Determine new version

Ask the user for the new version (e.g., `0.1.3`). If they just say "bump", increment the patch version.

### 2. Bump Python packages (4 packages)

Update `version = "X.Y.Z"` in:
- `python/hush-icore/pyproject.toml`
- `python/hush-providers/pyproject.toml`
- `python/hush-telemetry/pyproject.toml`
- `python/hush-serve/pyproject.toml`

Also update cross-package dependency versions:
- `hush-icore >= X.Y.Z` in hush-providers, hush-telemetry, hush-serve
- `hush-providers >= X.Y.Z` in hush-serve (optional)
- `hush-telemetry >= X.Y.Z` in hush-serve (optional)

### 3. Bump Rust crates (6 crates)

Update `version = "X.Y.Z"` in:
- `rust/hush-plugin/Cargo.toml`
- `rust/hush-icore/Cargo.toml`
- `rust/hush-providers/Cargo.toml`
- `rust/hush-telemetry/Cargo.toml`
- `rust/hush-serve/Cargo.toml`
- `rust/ui-hush-eyes/Cargo.toml` (hush-eyes)

Also update inter-crate dependency versions in each Cargo.toml.

### 4. Run tests

```bash
# Python
cd python/hush-icore && uv run -m pytest
cd python/hush-providers && uv run -m pytest
cd python/hush-serve && uv run -m pytest

# Rust
cd rust && cargo test --workspace
```

### 5. Commit and PR

```bash
git add -A
git commit -m "chore: bump all packages to vX.Y.Z"
git push origin dev
gh pr create --base main --head dev --title "chore: bump to vX.Y.Z" --body "..."
```

### 6. After merge

The Publish workflow (`.github/workflows/publish.yaml`) auto-triggers on merge to main:
- Publishes Python packages to PyPI (via `PYPI_API_TOKEN`)
- Publishes Rust crates to crates.io (via `CARGO_REGISTRY_TOKEN`)

Crates.io publish order (dependencies):
```
hush-plugin (standalone)
hush-providers (standalone)
hush-icore (depends on hush-providers)
hush-telemetry (depends on hush-icore)
hush-serve (depends on hush-icore + hush-providers + hush-telemetry)
hush-eyes (standalone)
```

### 7. Verify

```bash
pip index versions hush-icore
pip index versions hush-serve
# Check crates.io manually or via cargo search
```

## Git identity

Always commit as `Bruce Win <batman1m2001@gmail.com>`. Never add Co-Authored-By lines.
