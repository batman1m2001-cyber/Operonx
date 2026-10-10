"""`[[serve]]` is deprecated (DOORLESS_SERVICES_PLAN S3, gate G3): loading
a manifest with it warns once per process, naming the file and the
replacement; one without it never warns."""

from __future__ import annotations

import warnings

import pytest

from operonx.app import manifest as manifest_mod
from operonx.app.manifest import Manifest

pytestmark = pytest.mark.unit

WITH_SERVE = """
[project]
name = "old"

[[serve]]
name = "score"
kind = "http"
path = "/score"
graph = "scorer:score_flow"
"""

WITHOUT = """
[project]
name = "new"
app = "app.main:APP"

[[graph]]
name = "score_flow"
entry = "scorer:score_flow"

[tracing]
sinks = ["local"]
"""


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(manifest_mod, "_SERVE_WARNED", False)


def _load(tmp_path, text, name="operonx.toml"):
    path = tmp_path / name
    path.write_text(text)
    return Manifest.from_file(path)


def test_serve_blocks_warn_exactly_once_per_process(tmp_path):
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _load(tmp_path, WITH_SERVE)
        _load(tmp_path, WITH_SERVE, "again.toml")
    deprecations = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert len(deprecations) == 1
    text = str(deprecations[0].message)
    assert "operonx.toml" in text and "Service(" in text and "2.0" in text


def test_a_manifest_without_serve_blocks_does_not_warn(tmp_path):
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        m = _load(tmp_path, WITHOUT)
    assert not [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert m.serves == () and [g.name for g in m.graphs] == ["score_flow"]


def test_serve_blocks_still_load(tmp_path):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        m = _load(tmp_path, WITH_SERVE)
    assert [s.name for s in m.serves] == ["score"]
