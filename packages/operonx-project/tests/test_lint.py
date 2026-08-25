"""Convention checks — and, as importantly, what they must NOT flag."""

from __future__ import annotations

from pathlib import Path

import pytest
from operonx_project.cli import main, suggest_manifest
from operonx_project.lint import lint_source

pytestmark = pytest.mark.unit

HERE = Path("m.py")


def rules(src: str) -> list[str]:
    return [f.rule for f in lint_source(src, HERE)]


def only(src: str):
    found = lint_source(src, HERE)
    assert len(found) == 1, [str(f) for f in found]
    return found[0]


class TestC3UnstableNames:
    def test_clean_assignment_is_fine(self):
        assert (
            rules("""
@op
def step(x): ...

@graph
def flow(v):
    a = step(x=v)
    START >> a >> END
""")
            == []
        )

    def test_op_in_argument_position_is_flagged(self):
        f = only("""
@op
def inner(x): ...
@op
def outer(y): ...

@graph
def flow(v):
    a = outer(y=inner(x=v))
    START >> a >> END
""")
        assert f.rule == "C3" and "UUID" in f.message

    @pytest.mark.parametrize("target", ["a, b =", "obj.attr =", "d['k'] ="])
    def test_targets_auto_name_rejects(self, target):
        """auto_name._parse_assignment rejects these, so identity is a UUID."""
        assert "C3" in rules(f"""
@op
def step(x): ...

@graph
def flow(v):
    {target} step(x=v), 1
""")

    def test_explicit_name_is_always_acceptable(self):
        assert (
            rules("""
@op
def step(x): ...

@graph
def flow(v):
    items = [step(x=v, name="s1")]
""")
            == []
        )

    def test_root_graph_in_main_is_not_flagged(self):
        """A throwaway instance handed to Operon() — nothing keys on its name."""
        assert (
            rules("""
@graph
def flow(v):
    ...

async def main():
    runs = [("a", flow(v=1)), ("b", flow(v=2))]
""")
            == []
        )

    def test_reassignment_inside_graph_is_flagged(self):
        assert "C3" in rules("""
@op
def step(x): ...

@graph
def flow(v):
    a = step(x=v)
    a = step(x=v)
""")


class TestC5Wiring:
    def test_op_built_in_loop_is_an_error(self):
        f = only("""
@op
def step(x): ...

@graph
def flow(items):
    for i in items:
        s = step(x=i)
""")
        assert f.rule == "C5" and f.severity == "error"

    def test_wiring_over_a_literal_is_only_a_warning(self):
        """for leaf in (a, b): leaf >> PARENT — topology is still static."""
        found = lint_source(
            """
@op
def step(x): ...

@graph
def flow(v):
    a = step(x=v)
    b = step(x=v)
    for leaf in (a, b):
        leaf >> PARENT
""",
            HERE,
        )
        assert [f.severity for f in found] == ["warning"]

    def test_wiring_over_a_dynamic_iterable_is_an_error(self):
        found = lint_source(
            """
@graph
def flow(nodes):
    for leaf in nodes:
        leaf >> PARENT
""",
            HERE,
        )
        assert [f.severity for f in found] == ["error"]

    def test_loop_outside_a_graph_is_ignored(self):
        assert (
            rules("""
def helper(items):
    for i in items:
        print(i)
""")
            == []
        )


class TestC6Resources:
    def test_literal_resource_is_fine(self):
        assert rules('@graph\ndef f(q):\n    x = LLMOp.of(resource="gpt-4o")\n') == []

    def test_list_of_literals_is_fine(self):
        """Load balancing across declared keys is still fully resolvable."""
        assert (
            rules('@graph\ndef f(q):\n    x = LLMOp.of(resource=["a", "b"], ratios=[0.7, 0.3])\n')
            == []
        )

    def test_dynamic_resource_warns_rather_than_errors(self):
        """`resource=agent.llm_resource` is deliberate injection, not a defect."""
        found = lint_source(
            "@graph\ndef f(q):\n    x = LLMOp.of(resource=agent.llm_resource)\n", HERE
        )
        assert [(f.rule, f.severity) for f in found] == [("C6", "warning")]

    def test_credential_read_directly_is_a_warning(self):
        f = only('import os\nk = os.getenv("OPENAI_API_KEY")\n')
        assert f.rule == "C6" and f.severity == "warning"

    def test_non_secret_env_read_is_ignored(self):
        assert rules('import os\nv = os.getenv("LOG_LEVEL")\n') == []


class TestRobustness:
    def test_syntax_error_is_reported_not_raised(self):
        f = only("def broken(:\n")
        assert f.rule == "E00"

    def test_unrelated_calls_are_not_mistaken_for_ops(self):
        assert (
            rules("""
@graph
def flow(v):
    x = str(v)
    y = compute(helper(v))
""")
            == []
        )


class TestSuggest:
    def test_drafts_entries_for_module_scope_graphs(self, tmp_path):
        (tmp_path / "wf.py").write_text("@graph\ndef flow(v): ...\n", encoding="utf-8")
        out = suggest_manifest(tmp_path)
        assert 'name  = "flow"' in out and 'entry = "wf:flow"' in out

    def test_drafts_bind_for_builder_functions(self, tmp_path):
        """The callbot shape: a plain function returning a nested @graph."""
        (tmp_path / "wf.py").write_text(
            """
def build_pipeline(agent):
    @graph
    def pipeline(x): ...
    return pipeline
""",
            encoding="utf-8",
        )
        out = suggest_manifest(tmp_path)
        assert 'entry = "wf:build_pipeline"' in out
        assert "[graph.bind]" in out and "agent =" in out

    def test_includes_resources_when_present(self, tmp_path):
        (tmp_path / "resources.yaml").write_text("llm:x: {}\n", encoding="utf-8")
        (tmp_path / "wf.py").write_text("@graph\ndef flow(v): ...\n", encoding="utf-8")
        assert 'overlay = "resources.yaml"' in suggest_manifest(tmp_path)


class TestCLI:
    def test_clean_project_exits_zero(self, tmp_path, capsys):
        (tmp_path / "wf.py").write_text("@op\ndef step(x): ...\n", encoding="utf-8")
        (tmp_path / "operonx.toml").write_text(
            '[project]\nname="d"\n[[graph]]\nname="g"\nentry="wf:step"\n', encoding="utf-8"
        )
        assert main([str(tmp_path)]) == 0
        assert "clean" in capsys.readouterr().out

    def test_missing_manifest_fails(self, tmp_path, capsys):
        (tmp_path / "wf.py").write_text("@op\ndef step(x): ...\n", encoding="utf-8")
        assert main([str(tmp_path)]) == 1
        assert "C1" in capsys.readouterr().out

    def test_no_manifest_flag_skips_c1(self, tmp_path):
        (tmp_path / "wf.py").write_text("@op\ndef step(x): ...\n", encoding="utf-8")
        assert main([str(tmp_path), "--no-manifest"]) == 0

    def test_warnings_alone_do_not_fail(self, tmp_path):
        (tmp_path / "wf.py").write_text('import os\nk = os.getenv("API_KEY")\n', encoding="utf-8")
        assert main([str(tmp_path), "--no-manifest"]) == 0

    def test_missing_path_exits_two(self, tmp_path):
        assert main([str(tmp_path / "nope")]) == 2
