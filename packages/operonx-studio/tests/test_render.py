"""Rendering.

Weighted toward the failure modes that make a diagram lie — a node that is
silently missing, an edge to something that does not exist, an inspector
that shows nothing useful — rather than toward markup details.
"""

from __future__ import annotations

import json
import re

import pytest
from operonx_studio.render import render_html, render_project

pytestmark = pytest.mark.unit


def ir(graphs, **kw):
    return {"ir_version": 1, "project": "demo", "graphs": graphs, **kw}


def graph(name="flow", nodes=(), edges=(), **kw):
    return {
        "name": name,
        "entry": f"main:{name}",
        "nodes": list(nodes),
        "edges": list(edges),
        "entries": [],
        "exits": [],
        **kw,
    }


def node(name, kind="FuncOp", **kw):
    return {"id": f"flow.{name}", "name": name, "kind": kind,
            "inputs": [], "outputs": [], "source": None, **kw}


class TestSelfContained:
    def test_no_external_resources(self):
        """A page that needs a CDN is useless offline, which is where you debug."""
        page = render_html(ir([graph(nodes=[node("a")])]))
        remote = re.findall(r'(?:src|href)\s*=\s*"(https?://[^"]+)"', page)
        assert remote == []

    def test_single_file_with_inline_data(self):
        page = render_html(ir([graph(nodes=[node("a")])]))
        assert "<script id=\"ir\"" in page and "</html>" in page


class TestNothingIsLost:
    def test_every_node_reaches_the_payload(self):
        names = ["alpha", "beta", "orphan"]
        payload = render_project(ir([graph(nodes=[node(n) for n in names],
                                           edges=[{"from": "alpha", "to": "beta"}])]))
        assert {n["name"] for n in payload["graphs"][0]["nodes"]} == set(names)

    def test_edges_never_point_at_a_missing_node(self):
        """START/END are boundaries; they must not become phantom nodes."""
        payload = render_project(ir([graph(
            nodes=[node("a")],
            edges=[{"from": "a", "to": "__END__"}, {"from": "__START__", "to": "a"}])]))
        g = payload["graphs"][0]
        ids = {n["id"] for n in g["nodes"]}
        assert all(e["src"] in ids and e["dst"] in ids for e in g["edges"])

    def test_every_drawn_edge_has_a_path(self):
        payload = render_project(ir([graph(
            nodes=[node("a"), node("b")], edges=[{"from": "a", "to": "b"}])]))
        assert all(e["d"] for e in payload["graphs"][0]["edges"])


class TestInspectorData:
    """'What feeds this node?' is the question the graph is opened to answer."""

    def test_ref_binding_names_producer_and_output(self):
        n = node("b", inputs=[{"name": "x", "required": True,
                               "binding": {"kind": "ref", "from": "flow.a", "output": "y",
                                           "transforms": 0}}])
        payload = render_project(ir([graph(nodes=[node("a"), n])]))
        shown = payload["graphs"][0]["nodes"][-1]["inputs"][0]
        assert shown["kind"] == "ref" and shown["text"] == "a.y"

    def test_transform_count_is_surfaced(self):
        n = node("b", inputs=[{"name": "x", "required": True,
                               "binding": {"kind": "ref", "from": "flow.a", "output": "y",
                                           "transforms": 2}}])
        text = render_project(ir([graph(nodes=[n])]))["graphs"][0]["nodes"][0]["inputs"][0]["text"]
        assert "2 transform" in text

    def test_scratch_binding(self):
        n = node("a", inputs=[{"name": "log", "required": True,
                               "binding": {"kind": "scratch", "key": "log"}}])
        text = render_project(ir([graph(nodes=[n])]))["graphs"][0]["nodes"][0]["inputs"][0]["text"]
        assert text == "SCRATCH['log']"

    def test_literal_and_unset(self):
        n = node("a", inputs=[
            {"name": "x", "binding": {"kind": "literal", "value": 7}},
            {"name": "y", "binding": {"kind": "unset"}}])
        shown = render_project(ir([graph(nodes=[n])]))["graphs"][0]["nodes"][0]["inputs"]
        assert shown[0]["text"] == "7" and shown[1]["text"] == "—"

    def test_source_anchor_prefers_where_it_was_wired(self):
        n = node("a", source={"defined_at": {"file": "ops.py", "line": 4},
                              "wired_at": {"file": "wf.py", "line": 30}})
        assert render_project(ir([graph(nodes=[n])]))["graphs"][0]["nodes"][0]["source"] == "wf.py:30"

    def test_source_falls_back_to_definition(self):
        n = node("a", source={"defined_at": {"file": "ops.py", "line": 4}})
        assert render_project(ir([graph(nodes=[n])]))["graphs"][0]["nodes"][0]["source"] == "ops.py:4"


class TestEdgeSemantics:
    def test_auto_softened_edge_is_distinguishable_from_authored(self):
        payload = render_project(ir([graph(
            nodes=[node("a"), node("b"), node("c")],
            edges=[{"from": "a", "to": "b", "soft": True, "origin": "auto_soft"},
                   {"from": "a", "to": "c", "origin": "authored"}])]))
        edges = {e["dst"].split(".")[-1]: e for e in payload["graphs"][0]["edges"]}
        assert edges["b"]["dash"] and edges["b"]["origin"] == "auto_soft"
        assert not edges["c"]["dash"]

    def test_condition_edge_is_styled_apart(self):
        payload = render_project(ir([graph(
            nodes=[node("a"), node("b")],
            edges=[{"from": "a", "to": "b", "type": "condition"}])]))
        assert payload["graphs"][0]["edges"][0]["colour"] == "var(--edge-cond)"


class TestLoops:
    def test_rewritten_cycles_are_announced(self):
        """Back-edges are deleted from the built graph — say so, don't hide it."""
        payload = render_project(ir([graph(
            nodes=[node("a")],
            rewritten_from={"__loop_0__": {"scc": ["a"], "back_edges": [["a", "a"]]}})]))
        assert "__loop_0__" in payload["graphs"][0]["rewritten"]

    def test_loop_node_carries_its_mode(self):
        g = graph(nodes=[node("__loop_0__", kind="GraphOp")],
                  loops={"flow.__loop_0__": {"mode": "synthetic", "until": None,
                                             "synthetic": True, "back_edges": []}})
        shown = render_project(ir([g]))["graphs"][0]["nodes"][0]
        assert shown["loop"]["mode"] == "synthetic"


class TestSafety:
    def test_a_name_cannot_break_out_of_the_data_block(self):
        """The payload lives in a <script> block; JSON does not escape '<'."""
        page = render_html(ir([graph(nodes=[node("</script><img src=x onerror=alert(1)>")])]))
        head, _, tail = page.partition('<script id="ir" type="application/json">')
        data_block, _, _ = tail.partition("</script>")
        assert "<" not in data_block and ">" not in data_block

    def test_escaped_payload_still_parses_as_json(self):
        import json as _json

        page = render_html(ir([graph(nodes=[node("a<b>c")])]))
        _, _, tail = page.partition('<script id="ir" type="application/json">')
        block, _, _ = tail.partition("</script>")
        assert _json.loads(block)["graphs"][0]["nodes"][0]["name"] == "a<b>c"

    def test_project_name_is_escaped_in_the_title(self):
        page = render_html(ir([graph(nodes=[node("a")])], project="<b>x</b>"))
        assert "<title><b>x</b>" not in page


class TestDeterminism:
    def test_same_ir_renders_identically(self):
        source = ir([graph(nodes=[node("a"), node("b")], edges=[{"from": "a", "to": "b"}])])
        assert render_html(source) == render_html(json.loads(json.dumps(source)))


class TestMultipleGraphs:
    def test_every_declared_graph_is_selectable(self):
        payload = render_project(ir([graph("one", [node("a")]), graph("two", [node("b")])]))
        assert [g["name"] for g in payload["graphs"]] == ["one", "two"]


class TestResourcesAndDeps:
    def test_resources_and_dependencies_reach_the_page(self):
        payload = render_project(ir(
            [graph(nodes=[node("a")])],
            resources={"keys": ["llm:gpt-4o"], "env": {"required": ["K"], "optional": {}}},
            dependencies={"declared": True, "name": "demo", "dependencies": ["operonx>=1.3.0"]},
        ))
        assert payload["resources"]["keys"] == ["llm:gpt-4o"]
        assert payload["dependencies"]["dependencies"] == ["operonx>=1.3.0"]

    def test_env_status_is_injected_not_taken_from_the_ir(self):
        """Machine state must not ride along inside the IR (breaks determinism)."""
        source = ir([graph(nodes=[node("a")])])
        assert render_project(source)["env_status"] == {}
        injected = render_project(source, {"K": {"set": True, "in_environment": True}})
        assert injected["env_status"]["K"]["set"] is True


class TestSecrets:
    def test_no_env_value_can_reach_the_page(self, tmp_path, monkeypatch):
        from operonx_studio.envstatus import env_status

        (tmp_path / ".env").write_text("OPENAI_API_KEY=sk-super-secret\n", encoding="utf-8")
        monkeypatch.setenv("OTHER_TOKEN", "also-secret")
        status = env_status(tmp_path, ["OPENAI_API_KEY", "OTHER_TOKEN"], [])

        page = render_html(ir([graph(nodes=[node("a")])]), env_status=status)
        assert "sk-super-secret" not in page
        assert "also-secret" not in page
        assert status["OPENAI_API_KEY"]["in_dotenv"] is True
        assert status["OTHER_TOKEN"]["in_environment"] is True


class TestGeneratedScript:
    """A JS syntax error renders a blank page, and no Python test would notice."""

    def test_inline_script_parses(self, tmp_path):
        import re
        import shutil
        import subprocess

        node = shutil.which("node")
        if node is None:
            pytest.skip("node not available")

        page = render_html(ir(
            [graph(nodes=[node_ := node_maker("a"), node_maker("b")],
                   edges=[{"from": "a", "to": "b", "soft": True, "origin": "auto_soft"}])],
            resources={"keys": ["llm:x"], "env": {"required": ["K"], "optional": {"L": "info"}}},
            dependencies={"declared": True, "name": "d", "dependencies": ["operonx"],
                          "extras": {"serve": ["fastapi"]}},
        ), env_status={"K": {"set": False, "in_environment": False, "in_dotenv": False}})

        script = re.findall(r"<script>(.*?)</script>", page, re.S)[0]
        target = tmp_path / "page.js"
        target.write_text(script, encoding="utf-8")
        result = subprocess.run([node, "--check", str(target)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr

    def test_exactly_one_inline_script(self):
        import re

        page = render_html(ir([graph(nodes=[node_maker("a")])]))
        assert len(re.findall(r"<script>", page)) == 1


def node_maker(name, kind="FuncOp", **kw):
    return node(name, kind, **kw)


class TestEditAffordances:
    """A shared file must not offer buttons that call an API it cannot reach."""

    def test_a_static_page_never_sets_the_flag(self):
        """The template reads the flag; only a serving daemon sets it."""
        page = render_html(ir([graph(nodes=[node_maker("a")])]))
        assert "__OPERONX_EDITABLE__ = true" not in page
        assert "__OPERONX_EDITABLE__ === true" in page, "the guard itself must ship"

    def test_the_controls_are_gated_on_that_flag(self):
        """The markup ships; whether it renders is decided at runtime."""
        page = render_html(ir([graph(nodes=[node_maker("a")])]))
        assert "function editable()" in page
        assert "if (!editable()) return ''" in page

    def test_edits_post_to_a_relative_path(self):
        """So the page works behind a prefix without knowing its own URL."""
        page = render_html(ir([graph(nodes=[node_maker("a")])]))
        assert "fetch('api/edit'" in page
        assert "fetch('http" not in page

    def test_a_preview_is_requested_before_applying(self):
        page = render_html(ir([graph(nodes=[node_maker("a")])]))
        assert "dry_run: !apply" in page
