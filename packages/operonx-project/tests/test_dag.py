import pytest

from operonx_project.dag import DagError, downstream, paths_overlap, topo_order, validate, waves
from operonx_project.model import Card


def cards(spec):
    return [Card(cid, "m", cid, deps=deps) for cid, deps in spec.items()]


def test_waves_are_depth():
    cs = cards({"c1": [], "c2": ["c1"], "c3": ["c1"], "c4": ["c2", "c3"], "c5": []})
    assert waves(cs) == {"c1": 1, "c5": 1, "c2": 2, "c3": 2, "c4": 3}


def test_validate_names_every_problem():
    cs = cards({"a": ["b"], "b": ["a"], "c": ["c"], "d": ["zz"]})
    with pytest.raises(DagError) as e:
        validate(cs, max_cards=3)
    text = " | ".join(e.value.problems)
    assert "c depends on itself" in text and "unknown card 'zz'" in text
    assert "cycle: a → b → a" in text and "more than max_cards=3" in text


def test_duplicate_ids_are_refused():
    with pytest.raises(DagError, match="duplicate"):
        validate([Card("a", "m", "a"), Card("a", "m", "a2")])


def test_downstream_and_topo_order():
    cs = cards({"a": [], "b": ["a"], "c": ["b"], "d": []})
    assert downstream(cs, "a") == {"b", "c"}
    order = topo_order(cs)
    assert order.index("a") < order.index("b") < order.index("c")


@pytest.mark.parametrize(
    "a,b,overlap",
    [
        (["src/a/**"], ["src/a/x.py"], True),
        (["src/a/*"], ["src/b/*"], False),
        (["src/a/x.py"], ["src/a/y.py"], False),
        (["**/*.py"], ["docs/x.md"], True),
        (["src/a"], ["src/a/x.py"], True),
        ([], ["src/a"], True),  # no paths declared: everything
        (["pyproject.toml"], ["src/**"], False),
    ],
)
def test_paths_overlap(a, b, overlap):
    assert paths_overlap(a, b) is overlap
