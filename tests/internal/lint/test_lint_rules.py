"""`operonx lint`'s static rules: each one flags its case and leaves the
right shape alone (C3, C5, C6 ported from operonx-studio; C7–C12 new)."""

from __future__ import annotations

from pathlib import Path

import pytest

from operonx.lint import lint_env, lint_path, lint_source

pytestmark = pytest.mark.unit

HERE = Path("src/feature/m.py")


def rules(src: str, path: Path = HERE) -> list:
    return [f.rule for f in lint_source(src, path)]


def only(src: str, path: Path = HERE):
    found = lint_source(src, path)
    assert len(found) == 1, [str(f) for f in found]
    return found[0]


GOOD = """
from operonx import END, START, graph, op


@op
def step(x):
    return {"y": x}


@graph
def flow(v):
    a = step(x=v)
    b = step(x=a["y"])
    START >> a >> b >> END
"""


def test_the_conventional_shape_is_clean():
    assert rules(GOOD) == []


# ── C3 ──


class TestC3:
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
        assert "C3" in rules(f"""
@op
def step(x): ...

@graph
def flow(v):
    {target} step(x=v), 1
    START >> END
""")

    def test_an_explicit_name_is_fine(self):
        assert "C3" not in rules("""
@op
def step(x): ...

@graph
def flow(v):
    items = [step(x=v, name="s1")]
    START >> items[0] >> END
""")

    def test_reassignment_inside_a_graph_is_flagged(self):
        assert "C3" in rules("""
@op
def step(x): ...

@graph
def flow(v):
    a = step(x=v)
    a = step(x=v)
    START >> a >> END
""")


# ── C5 ──


class TestC5:
    def test_an_op_built_in_a_loop_is_an_error(self):
        found = lint_source(
            """
@op
def step(x): ...

@graph
def flow(items):
    for i in items:
        s = step(x=i)
    START >> END
""",
            HERE,
        )
        assert [(f.rule, f.severity) for f in found] == [("C5", "error")]

    def test_wiring_over_a_literal_is_only_a_warning(self):
        found = lint_source(
            """
@op
def step(x): ...

@graph
def flow(v):
    a = step(x=v)
    b = step(x=v)
    START >> a >> END
    for leaf in (a, b):
        leaf >> PARENT
""",
            HERE,
        )
        assert [(f.rule, f.severity) for f in found] == [("C5", "warning")]


# ── C6 ──


class TestC6:
    def test_a_literal_resource_and_a_list_of_keys_are_fine(self):
        assert (
            rules('@graph\ndef f(q):\n    x = LLMOp.of(resource="a")\n    START >> x >> END\n')
            == []
        )
        assert (
            rules(
                '@graph\ndef f(q):\n    x = LLMOp.of(resource=["a", "b"])\n    START >> x >> END\n'
            )
            == []
        )

    def test_a_dynamic_resource_warns(self):
        found = lint_source(
            "@graph\ndef f(q):\n    x = LLMOp.of(resource=agent.r)\n    START >> x >> END\n", HERE
        )
        assert [(f.rule, f.severity) for f in found] == [("C6", "warning")]

    def test_a_credential_read_directly_warns_and_a_budget_does_not(self):
        f = only('import os\nk = os.getenv("OPENAI_API_KEY")\n')
        assert (f.rule, f.severity) == ("C6", "warning")
        assert (
            rules(
                'import os\nv = os.getenv("AGENT_DAILY_TOKENS")\nd = os.environ["MF_SECRETS_DIR"]\n'
            )
            == []
        )


# ── C7 ──


class TestC7:
    def test_a_graph_factory_is_flagged(self):
        f = only("""
def build(k):
    @graph
    def flow(v):
        START >> END
    return flow
""")
        assert f.rule == "C7" and "build" in f.message

    def test_a_graph_in_a_class_is_flagged(self):
        assert "C7" in rules("class Bot:\n    @graph\n    def flow(v):\n        START >> END\n")

    def test_a_module_level_graph_is_fine(self):
        assert "C7" not in rules(GOOD)


# ── C8 ──


class TestC8:
    def test_print_in_product_code_is_flagged(self):
        f = only('def f():\n    print("hi")\n')
        assert f.rule == "C8"

    @pytest.mark.parametrize(
        "path", ["tests/test_x.py", "conftest.py", "scripts/seed.py", "bench/load.py", "x_test.py"]
    )
    def test_tests_and_terminal_tools_may_print(self, path):
        assert rules('print("hi")\n', Path(path)) == []

    def test_a_line_can_opt_out(self):
        assert rules('print("hi")  # operonx: allow[C8]\n') == []
        assert rules('print("hi")  # operonx: allow[C10]\n') == ["C8"]


# ── C9 ──


class TestC9:
    def test_a_range_retry_loop_is_flagged(self):
        f = only("""
@op
def fetch(url):
    for attempt in range(3):
        try:
            return {"body": get(url)}
        except TimeoutError:
            pass
""")
        assert f.rule == "C9" and "fetch" in f.message

    def test_a_while_loop_that_sleeps_in_its_handler_is_flagged(self):
        assert "C9" in rules("""
@op
async def fetch(url):
    while True:
        try:
            body = await get(url)
            break
        except IOError:
            await asyncio.sleep(1)
    return {"body": body}
""")

    def test_skipping_a_bad_item_is_not_a_retry(self):
        assert (
            rules("""
@op
def parse(rows):
    out = []
    for row in rows:
        try:
            out.append(int(row))
        except ValueError:
            continue
    return {"out": out}
""")
            == []
        )

    def test_a_timer_tick_doing_work_after_its_try_is_not_a_retry(self):
        # the callbot's prompt_frames shape
        assert (
            rules("""
@op
async def tick(done):
    while not done.is_set():
        try:
            await asyncio.wait_for(done.wait(), timeout=0.2)
            return
        except asyncio.TimeoutError:
            pass
        now = clock()
        if now > limit:
            yield {"frame": now}
""")
            == []
        )

    def test_a_loop_outside_an_op_is_ignored(self):
        assert (
            rules(
                "def helper():\n    for attempt in range(3):\n        try:\n            return go()\n        except E:\n            pass\n"
            )
            == []
        )


# ── C10 ──


class TestC10:
    @pytest.mark.parametrize(
        "line,what",
        [
            ("if v:\n        a = step(x=v)", "`if`"),
            ("a = step(x=v) if v else None", "`if`"),
            ("try:\n        a = step(x=v)\n    except E:\n        a = None", "`try`"),
            ("with lock:\n        a = step(x=v)", "`with`"),
            ("cfg = open('x.json').read()", "open()"),
            ("p = Path('x').read_text()", "read_text"),
            ("e = os.environ.get('X')", "os."),
        ],
    )
    def test_logic_in_a_graph_body_is_flagged(self, line, what):
        found = [
            f
            for f in lint_source(f"@graph\ndef flow(v):\n    {line}\n    START >> END\n", HERE)
            if f.rule == "C10"
        ]
        assert found and what in found[0].message

    def test_if_branches_in_the_chain_are_fine(self):
        assert (
            rules("""
@op
def step(x): ...

@graph
def flow(v):
    a = step(x=v)
    b = step(x=v)
    START >> a >> if_(a["ok"], b).else_(END)
    b >> END
""")
            == []
        )

    def test_a_helper_function_in_graph_py_warns(self):
        found = lint_source("def helper(x):\n    return x\n", Path("src/f/graph.py"))
        assert [(f.rule, f.severity) for f in found] == [("C10", "warning")]
        assert lint_source("def helper(x):\n    return x\n", Path("src/f/ops.py")) == []


# ── C11 ──


class TestC11:
    def test_a_graph_without_start_and_end_is_flagged(self):
        f = only("@op\ndef step(x): ...\n\n@graph\ndef flow(v):\n    a = step(x=v)\n")
        assert f.rule == "C11" and "START or END" in f.message

    def test_a_graph_without_end_is_flagged(self):
        f = only(
            "@op\ndef step(x): ...\n\n@graph\ndef flow(v):\n    a = step(x=v)\n    START >> a\n"
        )
        assert f.rule == "C11" and "never wires END:" in f.message

    def test_end_inside_a_branch_counts(self):
        assert "C11" not in rules(
            "@graph\ndef flow(v):\n    a = step(x=v)\n    START >> a >> if_(a['x'], a).else_(END)\n"
        )

    def test_a_graph_that_builds_nothing_needs_no_edges(self):
        assert rules("@graph\ndef flow(v):\n    ...\n") == []


# ── C12 ──


class TestC12:
    def _project(self, tmp_path, resources: str, example=None, code=""):
        (tmp_path / "resources.yaml").write_text(resources)
        if example is not None:
            (tmp_path / ".env.example").write_text(example)
        src = tmp_path / "src" / "f"
        src.mkdir(parents=True)
        (src / "ops.py").write_text(code)
        return tmp_path

    def test_a_name_missing_from_env_example_is_flagged(self, tmp_path):
        root = self._project(
            tmp_path,
            "llm:a:\n  api_key: ${LLM_API_KEY}\n  base_url: ${LLM_BASE_URL:https://x}\n  # model: ${COMMENTED}\n",
            example="LLM_API_KEY=\n",
        )
        found = lint_env(root, root.rglob("*.py"))
        assert [(f.rule, f.message.split()[0]) for f in found] == [("C12", "LLM_BASE_URL")]

    def test_listed_names_pass_commented_ones_included(self, tmp_path):
        root = self._project(
            tmp_path,
            "llm:a:\n  api_key: ${LLM_API_KEY}\n  base_url: ${LLM_BASE_URL:https://x}\n",
            example="LLM_API_KEY=\n# LLM_BASE_URL=https://api.openai.com/v1\n",
        )
        assert lint_env(root, root.rglob("*.py")) == []

    def test_a_credential_read_in_code_must_be_listed(self, tmp_path):
        root = self._project(
            tmp_path, "", example="", code='import os\nk = os.environ.get("PAY_TOKEN")\n'
        )
        found = lint_env(root, list(root.rglob("*.py")))
        assert [f.rule for f in found] == ["C12"] and "PAY_TOKEN" in found[0].message

    def test_no_env_example_is_one_finding(self, tmp_path):
        root = self._project(tmp_path, "a:\n  k: ${A_KEY}\n  u: ${A_URL}\n  m: ${A_MODEL}\n")
        found = lint_env(root, [])
        assert len(found) == 1 and "3 environment name(s)" in found[0].message

    def test_lint_path_includes_c12(self, tmp_path):
        root = self._project(tmp_path, "a:\n  k: ${A_KEY}\n", example="")
        assert [f.rule for f in lint_path(root)] == ["C12"]


def test_a_syntax_error_is_reported_not_raised():
    assert only("def broken(:\n").rule == "E00"


def test_unrelated_calls_are_not_mistaken_for_ops():
    assert rules("@graph\ndef flow(v):\n    x = str(v)\n    START >> END\n") == []
