"""``operonx.agents`` / ``operonx.kb`` are the separately installed packages, by a short name."""

import importlib
import sys

import pytest

from operonx import _aliases

pytestmark = pytest.mark.unit


@pytest.fixture
def fake(tmp_path, monkeypatch):
    pkg = tmp_path / "fakeagents_pkg"
    (pkg / "sub").mkdir(parents=True)
    (pkg / "__init__.py").write_text("class Agent: pass\n")
    (pkg / "sub" / "__init__.py").write_text("")
    (pkg / "sub" / "deep.py").write_text("from fakeagents_pkg import Agent\nVALUE = 7\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setitem(_aliases.ALIASES, "operonx.fakeagents", ("fakeagents_pkg", "fake-agents"))
    yield
    for name in [n for n in sys.modules if n.startswith(("operonx.fakeagents", "fakeagents_pkg"))]:
        del sys.modules[name]


def test_the_short_name_is_the_same_module_object_down_to_submodules(fake):
    short = importlib.import_module("operonx.fakeagents.sub.deep")
    real = importlib.import_module("fakeagents_pkg.sub.deep")
    assert short is real and short.VALUE == 7
    from operonx.fakeagents import Agent

    assert Agent is importlib.import_module("fakeagents_pkg").Agent  # isinstance agrees


def test_a_missing_package_names_its_install(monkeypatch):
    monkeypatch.setitem(_aliases.ALIASES, "operonx.nothere", ("no_such_pkg_xyz", "no-such-pkg"))
    with pytest.raises(ImportError, match="pip install no-such-pkg"):
        importlib.import_module("operonx.nothere")


def test_the_real_aliases():
    assert _aliases.ALIASES["operonx.agents"] == ("operonx_agents", "operonx-agents")
    assert _aliases.ALIASES["operonx.kb"] == ("operonx_kb", "operonx-kb")
