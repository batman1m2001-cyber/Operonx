"""Project IR extraction.

The interesting cases are the ones the tutorial cannot show: a rewritten
cycle, a factory-built node, and values that must never be probed.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest
from operonx_project.extract import (
    IR_VERSION,
    ExtractError,
    _binding,
    _edge_origin,
    build_entry,
    extract_project,
)
from operonx_project.manifest import Manifest

from operonx.core import END, PARENT, SCRATCH, START, GraphOp, Operon, graph, op

pytestmark = pytest.mark.unit


_MODULE_SEQ = itertools.count()


def project(tmp_path: Path, source: str, manifest: str) -> Manifest:
    """Write a throwaway project, each with a unique module name.

    Top-level module names are global: resolving two projects that both
    define ``wf`` in one interpreter is exactly the collision the manifest
    loader refuses. One process, one project — including here.
    """
    module = f"wf_{next(_MODULE_SEQ)}"
    (tmp_path / f"{module}.py").write_text(source, encoding="utf-8")
    (tmp_path / "operonx.toml").write_text(
        manifest.replace("wf:", f"{module}:"), encoding="utf-8"
    )
    return Manifest.load(tmp_path)


LINEAR = '''
from operonx.core import END, PARENT, START, graph, op

@op
def first(x: int):
    return {"y": x + 1}

@op
def second(y: int):
    return {"z": y * 2}

@graph
def flow(x):
    a = first(x=x)
    b = second(y=a["y"])
    b["z"] >> PARENT["z"]
    START >> a >> b >> END
'''

LINEAR_MANIFEST = '[project]\nname="d"\n[[graph]]\nname="flow"\nentry="wf:flow"\n'


class TestShape:
    def test_ir_envelope(self, tmp_path):
        ir = extract_project(project(tmp_path, LINEAR, LINEAR_MANIFEST))
        assert ir["ir_version"] == IR_VERSION
        assert ir["project"] == "d"
        assert [g["name"] for g in ir["graphs"]] == ["flow"]

    def test_nodes_edges_and_boundary(self, tmp_path):
        g = extract_project(project(tmp_path, LINEAR, LINEAR_MANIFEST))["graphs"][0]
        assert [n["name"] for n in g["nodes"]] == ["a", "b"]
        assert [(e["from"], e["to"]) for e in g["edges"]] == [("a", "b")]
        assert g["entries"] == ["a"] and g["exits"] == ["b"]

    def test_node_ids_are_hierarchical_full_names(self, tmp_path):
        g = extract_project(project(tmp_path, LINEAR, LINEAR_MANIFEST))["graphs"][0]
        assert [n["id"] for n in g["nodes"]] == ["flow.a", "flow.b"]

    def test_graph_is_named_from_the_manifest_label(self, tmp_path):
        """auto_name reads the *caller's* frame — ours — unless we override."""
        m = project(tmp_path, LINEAR, '[project]\nname="d"\n[[graph]]\nname="my_label"\nentry="wf:flow"\n')
        assert build_entry(m.graphs[0], m.root).name == "my_label"

    def test_input_provenance(self, tmp_path):
        g = extract_project(project(tmp_path, LINEAR, LINEAR_MANIFEST))["graphs"][0]
        b = next(n for n in g["nodes"] if n["name"] == "b")
        binding = b["inputs"][0]["binding"]
        assert binding["kind"] == "ref"
        assert binding["from"] == "flow.a" and binding["output"] == "y"

    def test_json_serialisable(self, tmp_path):
        ir = extract_project(project(tmp_path, LINEAR, LINEAR_MANIFEST))
        json.dumps(ir)          # must not raise — Refs hold op objects, not names


class TestDeterminism:
    def test_two_extractions_agree_byte_for_byte(self, tmp_path):
        m = project(tmp_path, LINEAR, LINEAR_MANIFEST)
        a = json.dumps(extract_project(m), sort_keys=True)
        b = json.dumps(extract_project(m), sort_keys=True)
        assert a == b

    def test_no_object_ids_leak(self, tmp_path):
        text = json.dumps(extract_project(project(tmp_path, LINEAR, LINEAR_MANIFEST)))
        assert "0x" not in text


class TestSourceAnchors:
    def test_funcop_reports_where_it_is_defined(self, tmp_path):
        g = extract_project(project(tmp_path, LINEAR, LINEAR_MANIFEST))["graphs"][0]
        src = next(n for n in g["nodes"] if n["name"] == "a")["source"]
        assert src["defined_at"]["file"].startswith("wf_")

    def test_wired_at_comes_from_the_ast(self, tmp_path):
        """The only anchor a class-based op has — its class lives in operonx."""
        g = extract_project(project(tmp_path, LINEAR, LINEAR_MANIFEST))["graphs"][0]
        wired = next(n for n in g["nodes"] if n["name"] == "a")["source"]["wired_at"]
        assert wired["file"].startswith("wf_") and wired["line"] > 0


class TestBindings:
    """Values are described, never probed — Ref.__getattr__ fabricates (S9)."""

    def test_scratch_binding(self):
        assert _binding(SCRATCH["log"]) == {"kind": "scratch", "key": "log"}

    def test_literal_and_unset(self):
        assert _binding(7) == {"kind": "literal", "value": 7}
        assert _binding(None) == {"kind": "unset"}

    def test_unknown_object_is_repr_not_traversed(self):
        class Weird:
            def __getattr__(self, name):
                raise AssertionError(f"probed {name!r}")

        assert _binding(Weird())["kind"] == "opaque"

    def test_ref_is_read_through_slots(self):
        with GraphOp(name="g"):
            pass
        b = _binding(PARENT["thing"])
        assert b["kind"] == "ref" and b["output"] == "thing"


class TestEdgeOrigin:
    @pytest.mark.parametrize(
        "flags, expected",
        [
            ({}, "authored"),
            ({"soft": True}, "authored_soft"),
            ({"soft": True, "auto_soft": True}, "auto_soft"),
            ({"pinned_hard": True}, "pinned_hard"),
        ],
    )
    def test_origin_distinguishes_author_from_compiler(self, flags, expected):
        from operonx.core.configs.edge_config import EdgeConfig

        edge = EdgeConfig(from_node="a", to_node="b", **flags)
        assert _edge_origin(edge) == expected


CYCLIC = '''
from operonx.core import END, PARENT, START, graph, op
from operonx.core.ops.flow import if_

@op
def step(n: int = 0):
    return {"n": n + 1, "done": n >= 2}

@op
def finish(n: int):
    return {"total": n}

@graph
def looping(seed):
    s = step(n=seed)
    gate = if_(s["done"], "out").else_("s")
    out = finish(n=s["n"])
    s["n"] >> PARENT["n"]
    START >> s >> gate
    gate >> [out, s]
    out >> END
'''


class TestLoops:
    """Cycle rewriting DELETES back-edges, so _rewritten_from is the only record."""

    def test_synthetic_loop_is_reported(self, tmp_path):
        m = project(tmp_path, CYCLIC, '[project]\nname="d"\n[[graph]]\nname="looping"\nentry="wf:looping"\n')
        g = extract_project(m)["graphs"][0]
        loops = g.get("loops") or {}
        synthetic = [v for v in loops.values() if v["synthetic"]]
        assert synthetic, f"expected a rewritten cycle, got nodes {[n['name'] for n in g['nodes']]}"
        loop = synthetic[0]
        assert loop["mode"] == "synthetic"
        assert loop["back_edges"], "back-edges must survive in the IR"
        # A synthetic loop has no condition object — its exit lives in an
        # if_() inside the body, so a UI must point into the body instead.
        assert loop["until"] is None

    def test_rewritten_from_is_preserved(self, tmp_path):
        m = project(tmp_path, CYCLIC, '[project]\nname="d"\n[[graph]]\nname="looping"\nentry="wf:looping"\n')
        g = extract_project(m)["graphs"][0]
        assert g.get("rewritten_from"), "audit dict is the only decompile record"


class TestResources:
    def test_env_contract_is_derived(self, tmp_path):
        (tmp_path / "resources.yaml").write_text(
            "llm:main:\n"
            "  api_key: ${OPENAI_API_KEY}\n"
            "  base_url: ${BASE_URL:https://api.openai.com/v1}\n",
            encoding="utf-8",
        )
        m = project(
            tmp_path, LINEAR,
            '[project]\nname="d"\n[resources]\noverlay="resources.yaml"\n'
            '[[graph]]\nname="flow"\nentry="wf:flow"\n',
        )
        res = extract_project(m)["resources"]
        assert res["keys"] == ["llm:main"]
        assert res["env"]["required"] == ["OPENAI_API_KEY"]
        assert res["env"]["optional"] == {"BASE_URL": "https://api.openai.com/v1"}

    def test_secret_values_never_enter_the_ir(self, tmp_path):
        (tmp_path / "resources.yaml").write_text(
            "llm:main:\n  api_key: sk-do-not-leak\n", encoding="utf-8")
        m = project(
            tmp_path, LINEAR,
            '[project]\nname="d"\n[resources]\noverlay="resources.yaml"\n'
            '[[graph]]\nname="flow"\nentry="wf:flow"\n',
        )
        assert "sk-do-not-leak" not in json.dumps(extract_project(m))


class TestFactories:
    def test_bind_supplies_a_factory_built_node(self, tmp_path):
        """The callbot shape: nodes that do not exist until something is injected."""
        source = '''
from operonx.core import END, PARENT, START, graph, op

tag_value = "injected"

def build(tag):
    @op
    def tagger(x: int):
        return {"out": f"{tag}:{x}"}

    @graph
    def flow(x):
        t = tagger(x=x)
        t["out"] >> PARENT["out"]
        START >> t >> END
    return flow
'''
        m = project(tmp_path, source,
                    '[project]\nname="d"\n[[graph]]\nname="flow"\nentry="wf:build"\n'
                    '[graph.bind]\ntag="wf:tag_value"\n')
        g = extract_project(m)["graphs"][0]
        assert [n["name"] for n in g["nodes"]] == ["t"]


class TestFailures:
    def test_builder_error_names_the_graph(self, tmp_path):
        source = "def build(dep):\n    raise RuntimeError('boom')\n\nvalue = 1\n"
        m = project(tmp_path, source,
                    '[project]\nname="d"\n[[graph]]\nname="g"\nentry="wf:build"\n'
                    '[graph.bind]\ndep="wf:value"\n')
        with pytest.raises(ExtractError, match="graph 'g'.*boom"):
            extract_project(m)

    def test_construction_error_names_the_graph(self, tmp_path):
        m = project(tmp_path, "def flow(x):\n    raise ValueError('nope')\n", LINEAR_MANIFEST)
        with pytest.raises(ExtractError, match="graph 'flow'.*nope"):
            extract_project(m)

    def test_non_callable_entry(self, tmp_path):
        m = project(tmp_path, "flow = 42\n", LINEAR_MANIFEST)
        with pytest.raises(ExtractError):
            extract_project(m)


def test_extracted_graph_still_runs(tmp_path):
    """Extraction must not leave the graph in a state that cannot execute."""
    m = project(tmp_path, LINEAR, LINEAR_MANIFEST)
    built = build_entry(m.graphs[0], m.root)

    async def go():
        return await Operon(built).run(inputs={"x": 4})

    import asyncio

    assert asyncio.run(go())["z"] == 10


class TestBuilderAnchors:
    """A manifest entry names the builder; the body belongs to the graph inside it."""

    BUILDER = '''
from operonx.core import END, PARENT, START, graph, op

@op
def step(x: int):
    return {"y": x + 1}

def build(tag):
    @graph
    def inner(x):
        a = step(x=x)
        a["y"] >> PARENT["y"]
        START >> a >> END
    return inner

tag_value = "t"
'''

    def test_wired_at_resolves_through_the_builder(self, tmp_path):
        m = project(tmp_path, self.BUILDER,
                    '[project]\nname="d"\n[[graph]]\nname="g"\nentry="wf:build"\n'
                    '[graph.bind]\ntag="wf:tag_value"\n')
        node = extract_project(m)["graphs"][0]["nodes"][0]
        assert node["source"]["wired_at"]["line"] > 0
