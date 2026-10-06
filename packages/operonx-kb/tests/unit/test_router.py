"""The router's rules (operonx_kb.retrieval.router): relation questions, by kind."""

import pytest

from operonx_kb.retrieval.router import relation_rule


@pytest.mark.parametrize(
    "query, rule",
    [
        ("Who is the mother of the director of film Polish-Russian War?", "role chain"),
        ("Where was the composer of film Love Story 1999 born?", "role of a work"),
        ("Who is the mother of Nephalion's father?", "role chain"),
        ("Where is Ulrich Walter's employer headquartered?", "possessive role"),
        ("What county was Tim Dubois born in?", "place chain"),
        (
            "What is the birthplace of the person after whom São José dos Campos was named?",
            "place chain",
        ),
        ("Are Blurt (Magazine) and Maui Magazine from the same country?", "comparison"),
        ("Which film came out first, Blind Shaft or The Mask Of Fu Manchu?", "comparison"),
    ],
)
def test_relation_questions(query, rule):
    assert relation_rule(query) == rule


@pytest.mark.parametrize(
    "query",
    [
        "how many days of annual leave do we get",
        "What did the Broncos win in 2016?",
        "Nhân viên được nghỉ phép bao nhiêu ngày mỗi năm?",
        "",
    ],
)
def test_other_questions_are_not_routed(query):
    assert relation_rule(query) is None
