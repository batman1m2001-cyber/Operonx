"""KBFilter: the closed filter vocabulary, the index payload, and the reference predicate."""

from datetime import datetime, timezone

import pytest

from operonx_kb.errors import FilterError
from operonx_kb.model.collection import CollectionSpec
from operonx_kb.model.filter import KBFilter, index_payload, matches

SPEC = CollectionSpec(filterable={"dept": "keyword", "year": "int", "labels": "keyword[]"})
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def payload(**kw):
    base = dict(
        collection_id="hr",
        document_id="doc_a",
        tags=["policy", "vn"],
        acl=["team:hr"],
        mime="text/markdown",
        created_at=T0,
        metadata={"dept": "HR", "year": 2026, "labels": ["leave", "pay"], "other": 1},
    )
    base.update(kw)
    return index_payload(spec=SPEC, **base)


def test_payload_holds_the_fixed_fields_and_declared_fields_only():
    p = payload()
    assert p == {
        "kb_collection": "hr",
        "kb_document": "doc_a",
        "kb_tags": ["policy", "vn"],
        "kb_acl": ["team:hr"],
        "kb_mime": "text/markdown",
        "kb_created": T0.timestamp(),
        "kb_f_dept": "HR",
        "kb_f_year": 2026,
        "kb_f_labels": ["leave", "pay"],
    }


def test_payload_refuses_a_value_of_the_wrong_type():
    with pytest.raises(FilterError, match="year"):
        payload(metadata={"year": "soon"})


def test_a_missing_declared_field_is_none():
    assert payload(metadata={})["kb_f_dept"] is None


@pytest.mark.parametrize(
    "flt, expected",
    [
        (KBFilter(), True),
        (KBFilter(document_ids=["doc_a", "doc_b"]), True),
        (KBFilter(document_ids=["doc_b"]), False),
        (KBFilter(tags_any=["vn", "x"]), True),
        (KBFilter(tags_any=["x"]), False),
        (KBFilter(tags_all=["vn", "policy"]), True),
        (KBFilter(tags_all=["vn", "x"]), False),
        (KBFilter(acl_any=["team:hr", "user:1"]), True),
        (KBFilter(acl_any=["user:1"]), False),
        (KBFilter(mime_in=["text/markdown"]), True),
        (KBFilter(mime_in=["application/pdf"]), False),
        (KBFilter(created_after=T0), True),  # inclusive
        (KBFilter(created_before=T0), False),  # exclusive
        (KBFilter(fields={"dept": "HR"}), True),
        (KBFilter(fields={"dept": "IT"}), False),
        (KBFilter(fields={"dept": ["IT", "HR"]}), True),
        (KBFilter(fields={"year": 2026}), True),
        (KBFilter(fields={"labels": "pay"}), True),
        (KBFilter(fields={"labels": ["x", "leave"]}), True),
        (KBFilter(fields={"labels": "x"}), False),
        (KBFilter(tags_any=["vn"], fields={"dept": "IT"}), False),
    ],
)
def test_matches_is_the_reference_semantics(flt, expected):
    assert matches(flt.checked(SPEC), "hr", payload()) is expected


def test_another_collection_never_matches():
    assert matches(KBFilter().checked(SPEC), "it", payload()) is False


def test_an_undeclared_field_raises():
    with pytest.raises(FilterError, match="other"):
        KBFilter(fields={"other": 1}).checked(SPEC)


def test_a_field_value_of_the_wrong_type_raises():
    with pytest.raises(FilterError, match="year"):
        KBFilter(fields={"year": "2026"}).checked(SPEC)


@pytest.mark.parametrize("field", ["document_ids", "tags_any", "tags_all", "acl_any", "mime_in"])
def test_an_empty_list_is_refused_not_read_as_no_filter(field):
    with pytest.raises(ValueError, match="empty"):
        KBFilter(**{field: []})


def test_unknown_keys_are_refused():
    with pytest.raises(ValueError):
        KBFilter.model_validate({"tenant": "acme"})


def test_a_document_without_acl_is_closed_to_an_acl_filter():
    assert matches(KBFilter(acl_any=["team:hr"]).checked(SPEC), "hr", payload(acl=[])) is False
