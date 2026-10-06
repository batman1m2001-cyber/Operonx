"""Concept extraction and the concept graph's walk (PLAN G1, G3, G4)."""

import pytest

from operonx_kb.enrich.concepts import chunk_concepts, concepts_fingerprint, fold, names
from operonx_kb.retrieval.graph import ConceptGraph, generation


@pytest.mark.parametrize(
    "text, expected",
    [
        # punctuation ends a name; a parenthesis is punctuation
        ("Polish-Russian War (Wojna polsko-ruska) is a film.", ["polish-russian war", "wojna"]),
        # lowercase connectors stay inside a name, trailing ones are dropped
        (
            "He was Duke of Penthièvre and Ludwig van Beethoven of",
            ["duke of penthievre", "ludwig van beethoven"],
        ),
        # 'and' is not a connector: two names, not one
        (
            "the son of Princess Madeleine and Christopher O'Neill.",
            ["princess madeleine", "christopher o'neill"],
        ),
        # a sentence-initial function word is not part of a name
        ("The Beatles played. In Liverpool they met.", ["beatles", "liverpool"]),
        # digits continue a name, never start one
        ("Apollo 11 landed in 1969.", ["apollo 11"]),
        # Vietnamese capitalises proper names the same way; diacritics fold
        ("Ông Hồ Chí Minh sinh ra tại Nghệ An.", ["ho chi minh", "nghe an"]),
        ("a b c", []),
    ],
)
def test_names(text, expected):
    assert names(text) == expected


def test_a_heading_weighs_more_and_the_title_counts_once():
    got = chunk_concepts("Xawery Żuławski directed it. Xawery Żuławski also wrote it.",
                         ["Polish War", "Polish War", "Cast"], title_weight=3.0)  # fmt: skip
    assert got["xawery zuławski"] == 2.0  # two mentions in the text
    assert got["polish war"] == 3.0  # the title, once though it is repeated
    assert got["cast"] == 3.0
    assert fold(" Hà  Nội ") == "ha noi"


def test_the_fingerprint_follows_what_changes_the_concepts():
    assert concepts_fingerprint(3.0) == concepts_fingerprint(3.0) != concepts_fingerprint(2.0)


def _graph(rows, **kw):
    kw = {"max_df_share": 1.0, "max_df_min": 100, **kw}
    return ConceptGraph(rows, **kw)


CHAIN = [
    ("film", "d_film", "zulawski", 1.0),
    ("film", "d_film", "polish war", 3.0),
    ("person", "d_person", "zulawski", 3.0),
    ("person", "d_person", "braunek", 1.0),
    ("mother", "d_mother", "braunek", 3.0),
    ("other", "d_other", "warsaw", 3.0),
]


def test_the_walk_reaches_what_the_seed_links_to_and_nothing_unlinked():
    ranked = _graph(CHAIN).rank({"film": 1.0}, alpha=0.3, iterations=30)
    order = [c for c, _ in ranked]
    assert order[:3] == ["film", "person", "mother"]  # one hop, then two
    assert "other" not in order
    assert 0 < sum(s for _, s in ranked) < 1  # the rest of the mass is on concepts


def test_a_concept_in_too_many_chunks_links_nothing():
    rows = CHAIN + [(f"c{i}", f"d{i}", "zulawski", 1.0) for i in range(10)]
    g = _graph(rows, max_df_share=0.01, max_df_min=5)  # 12 chunks name it: over the limit
    assert g.dropped == 1
    assert "person" not in [c for c, _ in g.rank({"film": 1.0}, alpha=0.3, iterations=30)]


def test_a_filter_closes_the_documents_it_hides_to_the_walk():
    g = _graph(CHAIN)
    allowed = {"d_film", "d_mother", "d_other"}  # the bridge is hidden
    order = [c for c, _ in g.rank({"film": 1.0}, alpha=0.3, iterations=30, documents=allowed)]
    assert order == ["film"]  # neither the hidden chunk nor what only it links to
    assert g.rank({"person": 1.0}, alpha=0.3, iterations=30, documents=allowed) == []


def test_seeds_the_graph_does_not_hold_keep_their_place():
    ranked = _graph(CHAIN).rank({"film": 1.0, "nameless": 0.5}, alpha=0.3, iterations=30)
    assert "nameless" in [c for c, _ in ranked]
    assert _graph([]).rank({"x": 1.0}, alpha=0.3, iterations=5) == [("x", 1.0)]
    assert _graph(CHAIN).rank({}, alpha=0.3, iterations=5) == []


def test_the_generation_is_the_set_of_active_versions():
    assert generation(["b", "a"]) == generation(["a", "b"]) != generation(["a"])
