"""The architecture check (PROJECT_PRACTICES_PLAN D2): each rule on a small repo, and its output read by the
same comparison that judges failing tests."""

from __future__ import annotations

from operonx_project.checks import CheckResult, _failures, compare
from operonx_project.contracts import _matches, check
from operonx_project.practices import parse

LAYERS = {"layers": [{"name": "app", "paths": ["app/**"]}, {"name": "graph", "paths": ["src/**/graph.py"]},
                     {"name": "ops", "paths": ["src/**/ops.py"]},
                     {"name": "helpers", "paths": ["src/**/_*.py"]}]}  # fmt: skip


def _repo(root, files):
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    return root


def _ids(root, practices, files):
    return [v.id for v in check(_repo(root, files), parse(practices, source="t"), files=sorted(files))]


def test_globs_read_as_git_reads_them():
    assert _matches("src/a/ops.py", ["src/*/ops.py"]) and not _matches("src/a/b/ops.py", ["src/*/ops.py"])
    assert _matches("src/ops.py", ["src/**/ops.py"]) and _matches("src/a/b/ops.py", ["src/**/ops.py"])
    assert not _matches("src/agents/_shared/tools.py", ["src/**/_*.py"])  # a file in a _folder is not a _file
    assert _matches("tests/x/test_y.py", ["tests"]) and _matches("tests/x/test_y.py", ["tests/**"])


def test_layers_import_inward_only(tmp_path):
    files = {
        "app/main.py": "from tip.graph import flow\n",  # outer → inner: fine
        "src/tip/graph.py": "from tip.ops import split\nfrom tip import _money\n",
        "src/tip/ops.py": "from tip.graph import flow\n",  # inner → outer
        "src/tip/_money.py": "",
        "src/tip/__init__.py": "from tip.graph import flow\n",  # a package's re-exports are no layer
    }
    assert _ids(tmp_path, {"architecture": LAYERS}, files) == ["arch:layer:src/tip/ops.py->tip.graph"]


def test_an_inner_layer_never_imports_a_framework(tmp_path):
    arch = {"layers": [{"name": "adapters", "paths": ["src/**/adapters/**"]},
                       {"name": "domain", "paths": ["src/**/domain/**"]}],
            "forbid": [{"layer": "domain", "imports": ["sqlalchemy", "sqlalchemy.*"],
                        "why": "plain Python"}]}  # fmt: skip
    files = {"src/shop/domain/order.py": "from sqlalchemy.orm import Session\nimport dataclasses\n",
             "src/shop/adapters/db.py": "import sqlalchemy\n"}  # fmt: skip
    found = check(_repo(tmp_path, files), parse({"architecture": arch}, source="t"), files=sorted(files))
    assert [v.id for v in found] == ["arch:forbid:src/shop/domain/order.py->sqlalchemy.orm.Session"]
    assert "the domain layer must not: plain Python" in found[0].message


def test_a_context_is_reached_only_through_its_public_modules(tmp_path):
    practices = {"contexts": [{"name": "billing", "paths": ["src/billing/**"], "public": ["graph.py"]},
                              {"name": "orders", "paths": ["src/orders/**"]}]}  # fmt: skip
    files = {
        "src/billing/graph.py": "",
        "src/billing/ledger.py": "",
        "src/orders/graph.py": "from billing.graph import charge\nfrom billing.ledger import post\n",
        "app/main.py": "from billing.ledger import post\n",  # the app composes the contexts: no context of its own
    }
    assert _ids(tmp_path, practices, files) == ["arch:context:src/orders/graph.py->billing.ledger"]


def test_private_names_and_attributes_of_another_package(tmp_path):
    files = {
        "src/tip/ops.py": (
            "from operonx.core import _run\nfrom tip import _money\nimport json\n\n"
            "class Op:\n    def __init__(self):\n        self._n = 0\n\n    def go(self, state):\n"
            "        self._n += 1\n        state._run_policy = 'x'\n        return json._default_decoder\n"
        ),
        "src/tip/_money.py": "",
        "src/split/ops.py": "from tip._money import cents\nfrom tip.ops import _hidden\n",
        "src/__init__.py": "from src.tip import _money\n",  # the package the others are inside
    }
    assert _ids(tmp_path, {"architecture": {"private": "forbid"}}, files) == [
        "arch:private:src/split/ops.py->tip._money",
        "arch:private:src/split/ops.py->tip.ops._hidden",
        "arch:private:src/tip/ops.py->operonx.core._run",
        "arch:private:src/tip/ops.py->state._run_policy",
        "arch:private:src/tip/ops.py->json._default_decoder",
    ]


def test_tests_are_not_the_architecture_and_nothing_is_checked_by_default(tmp_path):
    files = {"tests/test_x.py": "import json\nx = json._default_decoder\n"}
    assert _ids(tmp_path, {"architecture": {"private": "forbid"}}, files) == []
    assert not parse({}, source="").enforced


def test_a_violation_keeps_its_id_when_code_moves_and_old_ones_are_the_past(tmp_path):
    practices = {"architecture": {"private": "forbid"}}
    before = check(_repo(tmp_path, {"m.py": "import json\nx = json._default_decoder\n"}), parse(practices), ["m.py"])
    moved = check(_repo(tmp_path, {"m.py": "import json\n\n\n\nx = json._default_decoder\ny = json._x\n"}),
                  parse(practices), ["m.py"])  # fmt: skip
    assert before[0].id == moved[0].id and before[0].line != moved[0].line

    def result(found):
        text = "".join(f"FAILED {v.id} - {v.message} (line {v.line})\n" for v in found)
        return CheckResult("architecture", False, 1, 0.1, text, failures=_failures(text))

    judged = compare([result(before)], [result(moved)])
    assert judged.new == ["architecture: arch:private:m.py->json._x"]  # the old one costs nothing
    assert compare([result(before)], [result(before)]).old == ["architecture"]
