"""Manifest parsing, validation, and marker behaviour."""

from __future__ import annotations

import pytest
from operonx_project import GraphSpec, Manifest, ManifestError

pytestmark = pytest.mark.unit


def write(tmp_path, body: str):
    (tmp_path / "operonx.toml").write_text(body, encoding="utf-8")
    return tmp_path


MINIMAL = """
[project]
name = "demo"

[[graph]]
name  = "flow"
entry = "main:flow"
"""


class TestLoad:
    def test_minimal(self, tmp_path):
        m = Manifest.load(write(tmp_path, MINIMAL))
        assert m.name == "demo"
        assert [g.name for g in m.graphs] == ["flow"]
        assert m.graphs[0].entry == "main:flow"
        assert m.graphs[0].bind == {} and m.graphs[0].inputs == {}

    def test_full(self, tmp_path):
        m = Manifest.load(
            write(
                tmp_path,
                """
[project]
name = "callbot"
description = "voice bot"

[resources]
base    = "common.yaml"
overlay = "resources.yaml"

[[graph]]
name  = "pipeline"
entry = "callbot.graph:build_pipeline"
inputs = { turn_id = 0 }
[graph.bind]
agent = "agents.hr:HRAgent"
""",
            )
        )
        assert m.description == "voice bot"
        assert m.resources.base == "common.yaml"
        g = m.graph("pipeline")
        assert g.bind == {"agent": "agents.hr:HRAgent"}
        assert g.inputs == {"turn_id": 0}

    def test_missing_file(self, tmp_path):
        with pytest.raises(ManifestError, match="no operonx.toml"):
            Manifest.load(tmp_path)

    def test_malformed_toml(self, tmp_path):
        with pytest.raises(ManifestError):
            Manifest.load(write(tmp_path, "[project\nname = 'x'"))

    @pytest.mark.parametrize(
        "body, match",
        [
            ("[project]\n", "name is required"),
            ('[project]\nname = "d"\n', "at least one"),
            ('[project]\nname = "d"\n[[graph]]\nentry = "m:f"\n', "missing 'name'"),
            ('[project]\nname = "d"\n[[graph]]\nname = "f"\n', "missing 'entry'"),
        ],
    )
    def test_structural_errors(self, tmp_path, body, match):
        with pytest.raises(ManifestError, match=match):
            Manifest.load(write(tmp_path, body))

    @pytest.mark.parametrize("ref", ["mainflow", "main:flow:extra", ":flow", "main:"])
    def test_bad_entry_reference_fails_at_parse(self, tmp_path, ref):
        """A typo is a manifest error, not a confusing ImportError later."""
        with pytest.raises(ManifestError, match="module:attr"):
            Manifest.load(
                write(tmp_path, f'[project]\nname="d"\n[[graph]]\nname="f"\nentry="{ref}"\n')
            )

    def test_duplicate_graph_names_rejected(self, tmp_path):
        with pytest.raises(ManifestError, match="duplicate"):
            Manifest.load(
                write(tmp_path, MINIMAL + '\n[[graph]]\nname="flow"\nentry="main:other"\n')
            )

    def test_graph_lookup_lists_alternatives(self, tmp_path):
        m = Manifest.load(write(tmp_path, MINIMAL))
        with pytest.raises(ManifestError, match="have: flow"):
            m.graph("nope")


class TestResolve:
    def test_resolves_from_project_root(self, tmp_path):
        (tmp_path / "mod_root.py").write_text("VALUE = 41\n", encoding="utf-8")
        m = Manifest.load(
            write(tmp_path, '[project]\nname="d"\n[[graph]]\nname="g"\nentry="mod_root:VALUE"\n')
        )
        assert m.graph("g").resolve(m.root) == 41

    def test_unimportable_module_names_the_graph(self, tmp_path):
        m = Manifest.load(
            write(tmp_path, '[project]\nname="d"\n[[graph]]\nname="g"\nentry="nope_xyz:f"\n')
        )
        with pytest.raises(ManifestError, match="graph 'g' entry.*nope_xyz"):
            m.graph("g").resolve(m.root)

    def test_missing_attribute_names_the_graph(self, tmp_path):
        (tmp_path / "mod_attr.py").write_text("OTHER = 1\n", encoding="utf-8")
        m = Manifest.load(
            write(tmp_path, '[project]\nname="d"\n[[graph]]\nname="g"\nentry="mod_attr:absent"\n')
        )
        with pytest.raises(ManifestError, match="no attribute 'absent'"):
            m.graph("g").resolve(m.root)

    def test_resolve_bind(self, tmp_path):
        (tmp_path / "dep.py").write_text("class Agent: pass\n", encoding="utf-8")
        m = Manifest.load(
            write(
                tmp_path,
                """
[project]
name = "d"
[[graph]]
name  = "g"
entry = "dep:Agent"
[graph.bind]
agent = "dep:Agent"
""",
            )
        )
        assert m.graph("g").resolve_bind(m.root)["agent"].__name__ == "Agent"

    def test_resolve_does_not_leak_sys_path(self, tmp_path):
        import sys

        (tmp_path / "mod_path.py").write_text("VALUE = 1\n", encoding="utf-8")
        m = Manifest.load(
            write(tmp_path, '[project]\nname="d"\n[[graph]]\nname="g"\nentry="mod_path:VALUE"\n')
        )
        before = list(sys.path)
        m.graph("g").resolve(m.root)
        assert sys.path == before


class TestResourceSpec:
    def test_files_in_merge_order_skipping_absent(self, tmp_path):
        (tmp_path / "resources.yaml").write_text("llm:x: {}\n", encoding="utf-8")
        m = Manifest.load(
            write(
                tmp_path,
                """
[project]
name = "d"
[resources]
base    = "absent.yaml"
overlay = "resources.yaml"
[[graph]]
name  = "g"
entry = "m:f"
""",
            )
        )
        assert [p.name for p in m.resources.files(m.root)] == ["resources.yaml"]

    def test_no_resources_declared(self, tmp_path):
        m = Manifest.load(write(tmp_path, MINIMAL))
        assert m.resources.files(m.root) == []


def test_graphspec_defaults_are_not_shared():
    """dataclass field(default_factory) — not a mutable class attribute."""
    a, b = GraphSpec("a", "m:a"), GraphSpec("b", "m:b")
    a.bind["x"] = "y"
    assert b.bind == {}


class TestForeignModuleCollision:
    """Projects share top-level module names — every example defines ``main``."""

    def test_module_from_another_project_is_refused(self, tmp_path):
        import sys
        import types

        other = tmp_path / "other_project"
        other.mkdir()
        (other / "main.py").write_text("flow = 1\n", encoding="utf-8")

        stale = types.ModuleType("main")
        stale.__file__ = str(other / "main.py")
        sys.modules["main"] = stale
        try:
            here = tmp_path / "here"
            here.mkdir()
            (here / "main.py").write_text("flow = 2\n", encoding="utf-8")
            (here / "operonx.toml").write_text(
                '[project]\nname="d"\n[[graph]]\nname="g"\nentry="main:flow"\n',
                encoding="utf-8",
            )
            m = Manifest.load(here)
            with pytest.raises(ManifestError, match="one project per"):
                m.graph("g").resolve(m.root)
        finally:
            sys.modules.pop("main", None)

    def test_module_from_this_project_is_allowed(self, tmp_path):
        (tmp_path / "mod_local.py").write_text("flow = 3\n", encoding="utf-8")
        (tmp_path / "operonx.toml").write_text(
            '[project]\nname="d"\n[[graph]]\nname="g"\nentry="mod_local:flow"\n',
            encoding="utf-8",
        )
        m = Manifest.load(tmp_path)
        assert m.graph("g").resolve(m.root) == 3
        assert m.graph("g").resolve(m.root) == 3  # second call uses the cache


class TestSourceRoots:
    """A project's packages need not sit at its root — callbot keeps them in src/."""

    def test_defaults_to_the_project_root(self, tmp_path):
        m = Manifest.load(write(tmp_path, MINIMAL))
        assert m.src == (".",) and m.graphs[0].src == (".",)

    def test_declared_roots_are_used_for_import(self, tmp_path):
        pkg = tmp_path / "src" / "deep_pkg"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text("", encoding="utf-8")
        (pkg / "mod.py").write_text("VALUE = 5\n", encoding="utf-8")
        m = Manifest.load(
            write(
                tmp_path,
                """
[project]
name = "d"
src  = ["src", "."]
[[graph]]
name  = "g"
entry = "deep_pkg.mod:VALUE"
""",
            )
        )
        assert m.src == ("src", ".")
        assert m.graph("g").resolve(m.root) == 5

    def test_a_single_string_is_accepted(self, tmp_path):
        m = Manifest.load(
            write(tmp_path, '[project]\nname="d"\nsrc="src"\n[[graph]]\nname="g"\nentry="m:f"\n')
        )
        assert m.src == ("src",)

    def test_failure_reports_which_roots_were_tried(self, tmp_path):
        m = Manifest.load(
            write(
                tmp_path,
                '[project]\nname="d"\nsrc=["src"]\n[[graph]]\nname="g"\nentry="absent_pkg:f"\n',
            )
        )
        with pytest.raises(ManifestError, match=r"Source roots tried: \['src'\]"):
            m.graph("g").resolve(m.root)

    def test_sys_path_is_left_unchanged(self, tmp_path):
        import sys

        pkg = tmp_path / "src"
        pkg.mkdir()
        (pkg / "mod_sp.py").write_text("VALUE = 1\n", encoding="utf-8")
        m = Manifest.load(
            write(
                tmp_path,
                '[project]\nname="d"\nsrc=["src"]\n[[graph]]\nname="g"\nentry="mod_sp:VALUE"\n',
            )
        )
        before = list(sys.path)
        m.graph("g").resolve(m.root)
        assert sys.path == before
