# Release Process

## Versioning

Hush sử dụng [Semantic Versioning](https://semver.org/):

- **MAJOR**: Breaking changes
- **MINOR**: New features (backwards compatible)
- **PATCH**: Bug fixes (backwards compatible)

```
0.1.0 → 0.1.1 (patch: bug fix)
0.1.1 → 0.2.0 (minor: new feature)
0.2.0 → 1.0.0 (major: breaking change)
```

## Package Dependencies

```
hush-core         (standalone)
    ↑
hush-providers    (depends on hush-core)
    ↑
hush-observability (depends on hush-core)
```

## Release Order

1. `hush-core` (if changed)
2. `hush-providers` (if changed)
3. `hush-observability` (if changed)

## Pre-release Checklist

### 1. Code Quality

```bash
# Run linter
ruff check .

# Format code
ruff format .

# Run tests
pytest tests/ -v

# Check coverage
pytest tests/ --cov=hush
```

### 2. Update Version

```python
# hush-core/pyproject.toml
[project]
version = "0.2.0"

# hush-providers/pyproject.toml
[project]
version = "0.2.0"
dependencies = [
    "hush-core>=0.2.0",  # Update dependency
]
```

### 3. Update Changelog

```markdown
# CHANGELOG.md

## [0.2.0] - 2024-01-15

### Added
- New MapNode for parallel iteration
- ResourceHub plugin system

### Changed
- Improved error messages

### Fixed
- Bug in BranchNode condition evaluation
```

### 4. Update Documentation

- Update API docs nếu có breaking changes
- Update examples nếu API thay đổi
- Update README với new features

## Build Package

### Using hatch

```bash
cd hush-core
hatch build

# Output:
# dist/
# ├── hush_core-0.2.0-py3-none-any.whl
# └── hush_core-0.2.0.tar.gz
```

### Verify Build

```bash
# Test install
pip install dist/hush_core-0.2.0-py3-none-any.whl

# Verify import
python -c "from hush.core import GraphNode; print('OK')"
```

## Publish to PyPI

### Test PyPI (Staging)

```bash
# Upload to test PyPI first
twine upload --repository testpypi dist/*

# Test install from test PyPI
pip install --index-url https://test.pypi.org/simple/ hush-core
```

### Production PyPI

```bash
# Upload to production PyPI
twine upload dist/*
```

### Using hatch

```bash
# Test PyPI
hatch publish -r test

# Production PyPI
hatch publish
```

## Git Tagging

```bash
# Create tag
git tag -a v0.2.0 -m "Release v0.2.0"

# Push tag
git push origin v0.2.0
```

## GitHub Release

1. Go to GitHub Releases
2. Click "Draft a new release"
3. Select tag `v0.2.0`
4. Title: `v0.2.0`
5. Body: Copy from CHANGELOG
6. Attach wheel/tarball files
7. Publish release

## Post-release

### Verify Installation

```bash
# Fresh environment
python -m venv test_env
source test_env/bin/activate

# Install from PyPI
pip install hush-core==0.2.0

# Test
python -c "from hush.core import GraphNode; print('Success!')"
```

### Announce

- Update documentation site
- Notify users (Discord/Slack/etc.)
- Update examples repository

## Hotfix Process

For critical bugs:

1. Create hotfix branch từ tag
```bash
git checkout -b hotfix/0.2.1 v0.2.0
```

2. Fix bug

3. Update version to `0.2.1`

4. Test thoroughly

5. Merge to main và create tag
```bash
git checkout main
git merge hotfix/0.2.1
git tag -a v0.2.1 -m "Hotfix v0.2.1"
git push origin main --tags
```

6. Build và publish

## CI/CD (Future)

```yaml
# .github/workflows/release.yml
name: Release

on:
  push:
    tags:
      - 'v*'

jobs:
  release:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3

      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.10'

      - name: Install hatch
        run: pip install hatch

      - name: Build
        run: hatch build

      - name: Publish to PyPI
        env:
          HATCH_INDEX_USER: __token__
          HATCH_INDEX_AUTH: ${{ secrets.PYPI_TOKEN }}
        run: hatch publish
```
