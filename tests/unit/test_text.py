import unicodedata

import pytest

from operonx_kb.text.normalize import clean_chars, normalize_block, normalize_inline
from operonx_kb.text.sentences import sentence_spans
from operonx_kb.text.tokenize import RegexTokenizer


def test_nfc_makes_decomposed_vietnamese_equal():
    decomposed = "Nghe\u0302\u0323 phép"
    assert decomposed != "Nghệ phép"
    assert normalize_inline(decomposed) == "Nghệ phép"
    assert unicodedata.is_normalized("NFC", normalize_inline(decomposed))


def test_inline_collapses_every_kind_of_space_and_drops_invisibles():
    assert normalize_inline(" a\t b c　d\n\ne​f­g﻿ ") == "a b c d efg"


def test_controls_dropped_but_newline_and_tab_survive_clean_chars():
    assert clean_chars("a\x00b\x07c\r\nd\re\tf") == "abc\nd\ne f"


def test_zero_width_joiner_is_kept():
    assert "‍" in normalize_inline("a‍b")


def test_block_keeps_indentation_and_trims_blank_edges():
    assert normalize_block("\n\n  x = 1   \n\ty\n\n") == "  x = 1\n y"


def test_regex_tokenizer_counts_words_punctuation_and_long_words():
    tok = RegexTokenizer(piece=6)
    assert tok.count("Hello, world!") == 4
    assert tok.count("internationalization") == 4  # 20 chars -> ceil(20/6)
    assert tok.count("") == 0


def test_tokenizer_fingerprint_depends_on_config():
    assert RegexTokenizer(6).fingerprint() == RegexTokenizer(6).fingerprint()
    assert RegexTokenizer(6).fingerprint() != RegexTokenizer(4).fingerprint()


def test_tokenizer_rejects_bad_piece():
    with pytest.raises(ValueError):
        RegexTokenizer(0)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("One. Two! Three?", ["One.", "Two!", "Three?"]),
        ("See e.g. this. Next one.", ["See e.g. this.", "Next one."]),
        ("Pi is 3.14 today. Yes.", ["Pi is 3.14 today.", "Yes."]),
        ("Nhân viên được nghỉ. Đăng ký trước.", ["Nhân viên được nghỉ.", "Đăng ký trước."]),
        ('He said "stop." Then left.', ['He said "stop."', "Then left."]),
        ("no end punctuation", ["no end punctuation"]),
        ("lower. case continues", ["lower. case continues"]),
    ],
)
def test_sentence_spans(text, expected):
    assert [text[s:e] for s, e in sentence_spans(text)] == expected


def test_sentence_spans_offset():
    spans = sentence_spans("Ab cd. Ef gh.", offset=10)
    assert spans == [(10, 16), (17, 23)]
