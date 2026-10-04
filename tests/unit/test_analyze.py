"""The lexical analyzer: what a word is, for BM25 (PLAN R2)."""

import unicodedata

import pytest

from operonx_kb.model.collection import AnalyzerSpec
from operonx_kb.text.analyze import Analyzer, fold_diacritics


def test_simple_casefolds_and_splits_on_anything_but_letters_and_digits():
    a = Analyzer(AnalyzerSpec())
    assert a.tokens("Annual-Leave: 12 days (v2_final)") == [
        "annual",
        "leave",
        "12",
        "days",
        "v2",
        "final",
    ]


def test_composed_and_decomposed_vietnamese_give_the_same_tokens():
    a = Analyzer(AnalyzerSpec())
    composed = "Nghỉ phép năm"
    decomposed = unicodedata.normalize("NFD", composed)
    assert decomposed != composed
    assert a.tokens(decomposed) == a.tokens(composed) == ["nghỉ", "phép", "năm"]


def test_folding_strips_tones_and_marks_and_maps_d_bar():
    assert fold_diacritics("Đà Nẵng nghỉ phép") == "Da Nang nghi phep"
    a = Analyzer(AnalyzerSpec(fold_diacritics=True))
    assert a.tokens("Đà Nẵng, nghỉ phép") == ["da", "nang", "nghi", "phep"]
    assert a.tokens("da nang nghi phep") == a.tokens("Đà Nẵng nghỉ phép")


def test_vi_adds_syllable_bigrams_within_a_phrase_only():
    a = Analyzer(AnalyzerSpec(kind="vi"))
    assert a.tokens("nghỉ phép năm") == ["nghỉ", "phép", "năm", "nghỉ_phép", "phép_năm"]
    # Punctuation ends a phrase: no bigram across it.
    assert a.tokens("phép. Năm") == ["phép", "năm"]


def test_vi_with_folding_folds_the_bigrams_too():
    a = Analyzer(AnalyzerSpec(kind="vi", fold_diacritics=True))
    assert a.tokens("Nghỉ phép") == ["nghi", "phep", "nghi_phep"]


def test_query_tokens_are_deduplicated_in_order():
    a = Analyzer(AnalyzerSpec())
    assert a.query_tokens("leave the leave policy") == ["leave", "the", "policy"]


def test_fingerprint_follows_the_config():
    fps = {
        Analyzer(AnalyzerSpec(kind=k, fold_diacritics=f)).fingerprint()
        for k in ("simple", "vi")
        for f in (False, True)
    }
    assert len(fps) == 4
    assert Analyzer(AnalyzerSpec()).fingerprint() == Analyzer(AnalyzerSpec()).fingerprint()


def test_unknown_kind_is_refused():
    with pytest.raises(ValueError):
        AnalyzerSpec(kind="stemmed")
