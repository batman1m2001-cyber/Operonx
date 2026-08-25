"""Typed edits to graph source.

The property that matters is the same one the config editors uphold: an
edit changes the token it names and nothing else. ``ast.unparse`` would
produce valid Python and destroy every comment and formatting choice in the
file, so these tests check what survives as much as what changes.
"""

from __future__ import annotations

import ast

import pytest
from operonx_project.pyedit import (
    PyEditError,
    delete_op,
    graph_names,
    insert_op_after,
    insert_op_between,
    op_names,
    rename_op,
    set_op_resource,
)

pytestmark = pytest.mark.unit

SOURCE = '''\
"""A workflow with things worth preserving."""

from operonx.core import END, PARENT, START, graph, op
from operonx.providers import LLMOp


@op
def clean(text: str):
    # keep this comment exactly where it is
    return {"cleaned": text.strip()}


@graph
def chat(question):
    """Docstring stays put."""
    pre = clean(text=question)          # aligned trailing comment
    llm = LLMOp.of(
        resource="gpt-4o-mini",
        prompt={"system": "Be terse.", "user": "{q}"},
        q=pre["cleaned"],
    )
    llm["content"] >> PARENT["answer"]
    START >> pre >> llm >> END


@graph
def other(question):
    llm = LLMOp.of(resource="gpt-4o", prompt={"user": "{q}"}, q=question)
    START >> llm >> END
'''


def parses(text: str) -> bool:
    try:
        ast.parse(text)
        return True
    except SyntaxError:
        return False


class TestNoOpIsByteIdentical:
    def test_setting_the_same_resource(self):
        assert set_op_resource(SOURCE, "chat", "llm", "gpt-4o-mini") == SOURCE

    def test_renaming_to_the_same_name(self):
        assert rename_op(SOURCE, "chat", "llm", "llm") == SOURCE


class TestSetResource:
    def test_only_the_literal_changes(self):
        out = set_op_resource(SOURCE, "chat", "llm", "claude-haiku")
        assert 'resource="claude-haiku"' in out or "resource='claude-haiku'" in out
        assert len(out.splitlines()) == len(SOURCE.splitlines())
        assert parses(out)

    def test_everything_around_it_survives(self):
        out = set_op_resource(SOURCE, "chat", "llm", "claude-haiku")
        assert "# keep this comment exactly where it is" in out
        assert "# aligned trailing comment" in out
        assert '"""Docstring stays put."""' in out
        assert '"""A workflow with things worth preserving."""' in out
        assert '"system": "Be terse."' in out

    def test_the_other_graph_is_untouched(self):
        out = set_op_resource(SOURCE, "chat", "llm", "claude-haiku")
        assert 'resource="gpt-4o"' in out, "same variable name in another graph"

    def test_a_list_of_resources(self):
        out = set_op_resource(SOURCE, "chat", "llm", ["a", "b"])
        assert 'resource=["a", "b"]' in out and parses(out)

    def test_quote_style_follows_the_file(self):
        double = '@graph\ndef g(q):\n    llm = LLMOp.of(resource="a")\n'
        single = "@graph\ndef g(q):\n    llm = LLMOp.of(resource='a')\n"
        assert 'resource="b"' in set_op_resource(double, "g", "llm", "b")
        assert "resource='b'" in set_op_resource(single, "g", "llm", "b")

    def test_computed_resource_is_refused(self):
        """`resource=agent.llm_resource` is injection; rewriting it severs it."""
        text = "@graph\ndef g(q):\n    llm = LLMOp.of(resource=agent.llm_resource)\n"
        with pytest.raises(PyEditError, match="computed"):
            set_op_resource(text, "g", "llm", "gpt-4o")

    def test_missing_resource_argument(self):
        with pytest.raises(PyEditError, match="no resource="):
            set_op_resource(SOURCE, "chat", "pre", "gpt-4o")

    def test_unknown_graph_and_op(self):
        with pytest.raises(PyEditError, match="no @graph"):
            set_op_resource(SOURCE, "absent", "llm", "x")
        with pytest.raises(PyEditError, match="no assignment"):
            set_op_resource(SOURCE, "chat", "absent", "x")


class TestRename:
    def test_definition_and_every_reference_move_together(self):
        out = rename_op(SOURCE, "chat", "pre", "cleaned_up")
        assert "cleaned_up = clean(text=question)" in out
        assert 'q=cleaned_up["cleaned"]' in out
        assert "START >> cleaned_up >> llm >> END" in out
        # "preserving" in the module docstring contains "pre"; check for the
        # identifier, not the substring.
        import re

        assert not re.search(r"\bpre\b", out)
        assert parses(out)

    def test_a_matching_word_inside_a_string_is_not_renamed(self):
        text = '@graph\ndef g(q):\n    pre = clean(text="pre and pre")\n    START >> pre >> END\n'
        out = rename_op(text, "g", "pre", "post")
        assert '"pre and pre"' in out
        assert "post = clean" in out and "START >> post >> END" in out

    def test_an_attribute_of_the_same_name_is_not_renamed(self):
        text = "@graph\ndef g(q):\n    pre = clean(x=cfg.pre)\n    START >> pre >> END\n"
        out = rename_op(text, "g", "pre", "post")
        assert "cfg.pre" in out and "post = clean" in out

    def test_the_other_graph_keeps_its_own_name(self):
        out = rename_op(SOURCE, "chat", "llm", "model")
        assert "model = LLMOp.of(" in out
        assert 'llm = LLMOp.of(resource="gpt-4o"' in out

    def test_collision_is_refused(self):
        """Two ops sharing a name: the second silently overwrites the first."""
        with pytest.raises(PyEditError, match="already used"):
            rename_op(SOURCE, "chat", "pre", "llm")

    def test_invalid_identifier_is_refused(self):
        with pytest.raises(PyEditError, match="not a valid"):
            rename_op(SOURCE, "chat", "pre", "not a name")

    def test_absent_name(self):
        with pytest.raises(PyEditError, match="no name"):
            rename_op(SOURCE, "chat", "absent", "x")


class TestIntrospection:
    def test_graph_names(self):
        assert graph_names(SOURCE) == ["chat", "other"]

    def test_nested_graphs_are_found(self):
        """callbot's graphs live inside builder functions."""
        text = "def build(agent):\n    @graph\n    def inner(x):\n        pass\n    return inner\n"
        assert graph_names(text) == ["inner"]

    def test_op_names_in_source_order(self):
        assert op_names(SOURCE, "chat") == ["pre", "llm"]

    def test_unparseable_source_reports_clearly(self):
        with pytest.raises(PyEditError, match="cannot parse"):
            graph_names("def broken(:\n")


CHAINED = '''\
from operonx.core import END, PARENT, START, graph, op


@graph
def flow(x):
    """Three in a row."""
    a = first(x=x)          # keep me
    b = second(y=a["y"])
    c = third(z=b["z"])
    c["out"] >> PARENT["out"]
    START >> a >> b >> c >> END
'''


class TestDeleteOp:
    def test_removes_the_assignment_and_rewires_the_chain(self):
        text = CHAINED.replace('    c = third(z=b["z"])\n', "    c = third(z=1)\n")
        text = text.replace('    c["out"] >> PARENT["out"]\n', "")
        out = delete_op(text, "flow", "b")
        assert "b = second" not in out
        assert "START >> a >> c >> END" in out
        assert parses(out)

    def test_surrounding_lines_survive(self):
        text = CHAINED.replace('    c = third(z=b["z"])\n', "    c = third(z=1)\n")
        text = text.replace('    c["out"] >> PARENT["out"]\n', "")
        out = delete_op(text, "flow", "b")
        assert "# keep me" in out and '"""Three in a row."""' in out

    def test_refuses_when_another_op_still_reads_it(self):
        """A smaller graph is not the same as a broken one."""
        with pytest.raises(PyEditError, match="still read by another op"):
            delete_op(CHAINED, "flow", "b")

    def test_drops_a_statement_left_with_nothing_to_connect(self):
        """`a >> END` has no other end once `a` goes; `START >> a >> END` does."""
        text = "@graph\ndef g(x):\n    a = f(x=x)\n    START >> a >> END\n    a >> END\n"
        out = delete_op(text, "g", "a")
        assert "a >> END" not in out, "one-operand statement must go"
        assert "START >> END" in out, "two ends still connect"
        assert parses(out)

    def test_unknown_op(self):
        with pytest.raises(PyEditError, match="no assignment"):
            delete_op(CHAINED, "flow", "absent")


class TestInsertOp:
    def test_lands_downstream_of_the_anchor(self):
        out = insert_op_after(CHAINED, "flow", "a", "mid", "middle(v=1)")
        assert "    mid = middle(v=1)\n" in out
        assert "START >> a >> mid >> b >> c >> END" in out
        assert parses(out)

    def test_indentation_matches_the_anchor(self):
        out = insert_op_after(CHAINED, "flow", "a", "mid", "middle(v=1)")
        assert "\n    mid = middle(v=1)\n" in out

    def test_the_rest_of_the_file_survives(self):
        out = insert_op_after(CHAINED, "flow", "a", "mid", "middle(v=1)")
        assert "# keep me" in out and '"""Three in a row."""' in out
        assert 'c["out"] >> PARENT["out"]' in out

    def test_name_collision_is_refused(self):
        with pytest.raises(PyEditError, match="already used"):
            insert_op_after(CHAINED, "flow", "a", "b", "middle()")

    def test_invalid_name_is_refused(self):
        with pytest.raises(PyEditError, match="not a valid"):
            insert_op_after(CHAINED, "flow", "a", "not a name", "middle()")

    def test_unknown_anchor(self):
        with pytest.raises(PyEditError, match="no assignment"):
            insert_op_after(CHAINED, "flow", "absent", "mid", "middle()")


class TestChainShapes:
    def test_a_list_operand_survives_verbatim(self):
        """`gate >> [ex, gd]` must not be reformatted by a chain rewrite."""
        text = (
            "@graph\ndef g(s):\n"
            "    gate = if_(s, 'ex')\n"
            "    ex = a()\n"
            "    gd = b()\n"
            "    drop = c()\n"
            "    START >> gate >> [ex, gd] >> END\n"
            "    START >> drop >> END\n"
        )
        out = delete_op(text, "g", "drop")
        assert "[ex, gd]" in out and "drop" not in out
        assert parses(out)

    def test_round_trip_of_an_insert_then_delete(self):
        once = insert_op_after(CHAINED, "flow", "a", "mid", "middle(v=1)")
        assert delete_op(once, "flow", "mid") == CHAINED


class TestInsertBetween:
    """The precise form: a '+' on one edge must not touch a sibling branch."""

    FORK = (
        "@graph\ndef g(x):\n"
        "    a = f(x=x)\n"
        "    b = s(v=1)\n"
        "    note = EmitOp(payload=1)\n"
        "    START >> a >> b >> END\n"
        "    a >> note >> END\n"
    )

    def test_only_the_named_edge_moves(self):
        out = insert_op_between(self.FORK, "g", "a", "b", "mid", "m()")
        assert "START >> a >> mid >> b >> END" in out
        assert "a >> note >> END" in out, "the sibling branch must be untouched"
        assert parses(out)

    def test_insert_after_would_have_hit_both(self):
        """Contrast, so the difference between the two is pinned down."""
        out = insert_op_after(self.FORK, "g", "a", "mid", "m()")
        assert "a >> mid >> note >> END" in out

    def test_a_non_edge_is_refused(self):
        with pytest.raises(PyEditError, match="not an edge"):
            insert_op_between(self.FORK, "g", "a", "note_absent", "mid", "m()")
        with pytest.raises(PyEditError, match="not an edge"):
            insert_op_between(self.FORK, "g", "b", "a", "mid", "m()")

    def test_round_trips_with_delete(self):
        once = insert_op_between(self.FORK, "g", "a", "b", "mid", "m()")
        assert delete_op(once, "g", "mid") == self.FORK


class TestBuilderEntries:
    """A manifest names the builder; the body is the @graph inside it."""

    BUILDER = (
        "def build_pipeline(agent):\n"
        "    @graph\n"
        "    def pipeline(x):\n"
        "        a = first(x=x)\n"
        "        START >> a >> END\n"
        "    return pipeline\n"
    )

    def test_edits_resolve_through_the_builder_name(self):
        out = rename_op(self.BUILDER, "build_pipeline", "a", "step")
        assert "step = first(x=x)" in out and "START >> step >> END" in out

    def test_the_graph_name_still_works(self):
        assert rename_op(self.BUILDER, "pipeline", "a", "step") == rename_op(
            self.BUILDER, "build_pipeline", "a", "step"
        )

    def test_graph_names_lists_the_graph_not_the_builder(self):
        assert graph_names(self.BUILDER) == ["pipeline"]
