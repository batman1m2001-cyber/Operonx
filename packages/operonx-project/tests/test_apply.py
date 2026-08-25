"""Planning and applying an edit the way the UI addresses it.

The UI knows a graph by its manifest label and a node by its short name;
these tests fix the bridge between that and a file on disk, and the rule
that nothing is written until someone has seen the diff.
"""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest
from operonx_project.apply import EditPlan, PlanError, apply_plan, plan_edit
from operonx_project.manifest import Manifest
from operonx_project.pyedit import PyEditError

pytestmark = pytest.mark.unit

_SEQ = itertools.count()

WORKFLOW = '''\
from operonx.core import END, PARENT, START, graph, op
from operonx.providers import LLMOp


@graph
def chat(question):
    pre = clean(text=question)      # keep this
    llm = LLMOp.of(resource="gpt-4o-mini", q=pre["cleaned"])
    START >> pre >> llm >> END
'''

BUILDER = '''\
from operonx.core import END, PARENT, START, graph


def build(agent):
    @graph
    def pipeline(x):
        a = first(x=x)
        START >> a >> END
    return pipeline


agent = object()
'''


@pytest.fixture
def project(tmp_path):
    mod = f"ap_{next(_SEQ)}"
    (tmp_path / f"{mod}.py").write_text(WORKFLOW, encoding="utf-8")
    (tmp_path / "operonx.toml").write_text(
        f'[project]\nname="d"\n[[graph]]\nname="Chat Flow"\nentry="{mod}:chat"\n',
        encoding="utf-8",
    )
    return Manifest.load(tmp_path), tmp_path / f"{mod}.py"


class TestPlanning:
    def test_a_plan_does_not_touch_the_file(self, project):
        manifest, path = project
        before = path.read_text(encoding="utf-8")
        plan_edit(manifest, "Chat Flow", "rename", old="pre", new="cleaned")
        assert path.read_text(encoding="utf-8") == before

    def test_the_plan_carries_a_readable_diff(self, project):
        manifest, _ = project
        plan = plan_edit(manifest, "Chat Flow", "rename", old="pre", new="cleaned")
        assert plan.changed
        assert "-    pre = clean" in plan.diff
        assert "+    cleaned = clean" in plan.diff

    def test_a_no_op_plan_has_an_empty_diff(self, project):
        manifest, _ = project
        plan = plan_edit(manifest, "Chat Flow", "set_resource", op_name="llm",
                         resource="gpt-4o-mini")
        assert not plan.changed and plan.diff == ""

    def test_the_label_is_resolved_not_the_function_name(self, project):
        """The UI addresses graphs by manifest label, which may differ."""
        manifest, path = project
        plan = plan_edit(manifest, "Chat Flow", "rename", old="pre", new="cleaned")
        assert plan.file == path and plan.graph == "Chat Flow"

    def test_unknown_action(self, project):
        manifest, _ = project
        with pytest.raises(PlanError, match="unknown action"):
            plan_edit(manifest, "Chat Flow", "explode", op_name="x")

    def test_unknown_graph_label(self, project):
        manifest, _ = project
        with pytest.raises(PlanError, match="no graph"):
            plan_edit(manifest, "Nope", "rename", old="pre", new="x")

    def test_an_inapplicable_edit_surfaces_the_reason(self, project):
        manifest, _ = project
        with pytest.raises(PyEditError, match="already used"):
            plan_edit(manifest, "Chat Flow", "rename", old="pre", new="llm")


class TestApplying:
    def test_apply_writes_and_reports(self, project):
        manifest, path = project
        plan = plan_edit(manifest, "Chat Flow", "rename", old="pre", new="cleaned")
        assert apply_plan(plan) is True
        assert "cleaned = clean" in path.read_text(encoding="utf-8")

    def test_the_rest_of_the_file_survives(self, project):
        manifest, path = project
        apply_plan(plan_edit(manifest, "Chat Flow", "rename", old="pre", new="cleaned"))
        assert "# keep this" in path.read_text(encoding="utf-8")

    def test_a_no_op_writes_nothing(self, project):
        manifest, path = project
        stamp = path.stat().st_mtime_ns
        plan = plan_edit(manifest, "Chat Flow", "set_resource", op_name="llm",
                         resource="gpt-4o-mini")
        assert apply_plan(plan) is False
        assert path.stat().st_mtime_ns == stamp

    def test_a_file_changed_since_planning_is_refused(self, project):
        """The daemon reloads on every save; a concurrent editor is normal."""
        manifest, path = project
        plan = plan_edit(manifest, "Chat Flow", "rename", old="pre", new="cleaned")
        path.write_text(WORKFLOW + "\n# someone else edited\n", encoding="utf-8")
        with pytest.raises(PlanError, match="changed since"):
            apply_plan(plan)
        assert "# someone else edited" in path.read_text(encoding="utf-8")

    def test_plan_apply_replan_round_trips(self, project):
        manifest, path = project
        original = path.read_text(encoding="utf-8")
        apply_plan(plan_edit(manifest, "Chat Flow", "rename", old="pre", new="cleaned"))
        apply_plan(plan_edit(manifest, "Chat Flow", "rename", old="cleaned", new="pre"))
        assert path.read_text(encoding="utf-8") == original


class TestBuilderEntry:
    def test_a_builder_entry_is_located_and_edited(self, tmp_path):
        """callbot's shape: the manifest names the builder, not the graph."""
        mod = f"ap_{next(_SEQ)}"
        (tmp_path / f"{mod}.py").write_text(BUILDER, encoding="utf-8")
        (tmp_path / "operonx.toml").write_text(
            f'[project]\nname="d"\n[[graph]]\nname="P"\nentry="{mod}:build"\n'
            f'[graph.bind]\nagent="{mod}:agent"\n',
            encoding="utf-8",
        )
        manifest = Manifest.load(tmp_path)
        plan = plan_edit(manifest, "P", "rename", old="a", new="step")
        assert "step = first(x=x)" in plan.after
        assert "START >> step >> END" in plan.after


class TestSourceRoots:
    def test_a_module_under_src_is_found(self, tmp_path):
        """callbot keeps packages in src/; the plan must follow src roots."""
        pkg = tmp_path / "src" / "wf_pkg"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text("", encoding="utf-8")
        (pkg / "flow.py").write_text(WORKFLOW, encoding="utf-8")
        (tmp_path / "operonx.toml").write_text(
            '[project]\nname="d"\nsrc=["src","."]\n'
            '[[graph]]\nname="C"\nentry="wf_pkg.flow:chat"\n',
            encoding="utf-8",
        )
        manifest = Manifest.load(tmp_path)
        plan = plan_edit(manifest, "C", "rename", old="pre", new="cleaned")
        assert plan.file == (pkg / "flow.py").resolve()

    def test_a_missing_module_is_reported(self, tmp_path):
        (tmp_path / "operonx.toml").write_text(
            '[project]\nname="d"\n[[graph]]\nname="C"\nentry="absent.mod:chat"\n',
            encoding="utf-8",
        )
        with pytest.raises(PlanError, match="cannot locate source"):
            plan_edit(Manifest.load(tmp_path), "C", "rename", old="a", new="b")


def test_plan_is_immutable():
    plan = EditPlan(file=Path("x.py"), graph="g", action="rename", before="a", after="b")
    with pytest.raises(Exception):
        plan.after = "c"  # type: ignore[misc]
