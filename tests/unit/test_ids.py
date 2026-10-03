from operonx_kb.model.ids import (
    canonical_json,
    chunk_id,
    combine_fingerprints,
    document_id,
    element_id,
    fingerprint,
    make_id,
    sha256_bytes,
    sha256_text,
    text_sha,
    version_id,
)


def test_ids_are_typed_128_bit_and_deterministic():
    d = document_id("handbook", "policy.pdf")
    assert d.startswith("doc_") and len(d) == 4 + 32
    assert d == document_id("handbook", "policy.pdf")
    assert d != document_id("handbook2", "policy.pdf")
    v = version_id(d, "a" * 64, "fp")
    assert v.startswith("ver_") and v == version_id(d, "a" * 64, "fp")
    assert element_id(v, "0.1").startswith("el_")
    assert chunk_id(d, "fp", "sha", 0) != chunk_id(d, "fp", "sha", 1)


def test_parts_are_separated_so_concatenations_do_not_collide():
    assert make_id("x", "ab", "c") != make_id("x", "a", "bc")


def test_text_sha_ignores_composition_and_whitespace_but_sha256_text_does_not():
    assert text_sha("Nghe\u0302\u0323  phép") == text_sha("Nghệ phép")
    assert sha256_text("a b") != sha256_text("a  b")
    assert sha256_bytes(b"abc") == sha256_text("abc")


def test_fingerprint_changes_with_any_input_and_ignores_key_order():
    base = fingerprint("c", "1", {"a": 1, "b": 2})
    assert base == fingerprint("c", "1", {"b": 2, "a": 1})
    assert base != fingerprint("c", "2", {"a": 1, "b": 2})
    assert base != fingerprint("c", "1", {"a": 1, "b": 3})
    assert base != fingerprint("d", "1", {"a": 1, "b": 2})
    assert combine_fingerprints(x="1", y="2") == combine_fingerprints(y="2", x="1")


def test_canonical_json_is_stable():
    assert canonical_json({"b": [1, "é"], "a": None}) == '{"a":null,"b":[1,"é"]}'
