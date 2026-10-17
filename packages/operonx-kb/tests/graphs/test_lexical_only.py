"""A collection with only a lexical index: ingest, re-ingest, edit, search, delete and GC
run with no embedder and no vector store (where the text may not leave for an embedding
service, or none is configured)."""

import asyncio

import pytest

from operonx_kb import CatalogError, CollectionSpec, QueryError
from operonx_kb.model.collection import AnalyzerSpec, LexicalIndexSpec


def run(coro):
    return asyncio.run(coro)


GUIDE = """# Thanh khoản dự phòng

Số tháng chi tiêu mà các tài sản dễ rút đủ trả; mốc an toàn là 6 tháng.

# Bài kiểm tra khẩu vị rủi ro

Tám đến mười hai câu tình huống, câu sau tùy câu trước.
"""


@pytest.fixture
def notes(kbx):
    kbx.create_collection(
        "notes",
        CollectionSpec(
            lexical=LexicalIndexSpec(analyzer=AnalyzerSpec(kind="vi", fold_diacritics=True))
        ),
    )
    return kbx


def test_a_lexical_only_collection_ingests_searches_and_never_embeds(notes, tmp_path):
    path = tmp_path / "guide.md"
    path.write_text(GUIDE, encoding="utf-8")
    added = run(notes.add("notes", str(path), key="guide.md", acl=["user:a"]))
    assert added["action"] == "new"
    assert added["stats"]["embedding"] == {"embedded": 0, "cached": 0, "calls": 0}
    assert added["stats"]["lexical"]["written"] > 0
    assert run(notes.add("notes", str(path), key="guide.md", acl=["user:a"]))["action"] == "skip"

    assert notes.default_mode("notes") == "lexical"
    hits = run(notes.search("notes", "thanh khoan du phong bao nhieu thang", k=2,
                            filter={"acl_any": ["user:a"]}))["hits"]  # fmt: skip
    assert "mốc an toàn là 6 tháng" in hits[0]["text"]
    assert run(notes.search("notes", "thanh khoan", filter={"acl_any": ["user:b"]}))["hits"] == []
    with pytest.raises(QueryError):
        run(notes.search("notes", "thanh khoan", mode="dense"))
    assert notes.verify("notes").ok


def test_an_edit_replaces_the_passage_and_delete_purge_leaves_nothing(notes, tmp_path):
    path = tmp_path / "guide.md"
    path.write_text(GUIDE, encoding="utf-8")
    run(notes.add("notes", str(path), key="guide.md"))
    path.write_text(GUIDE.replace("6 tháng", "9 tháng"), encoding="utf-8")
    assert run(notes.add("notes", str(path), key="guide.md"))["action"] == "update"
    top = run(notes.search("notes", "moc an toan", k=1))["hits"][0]
    assert "9 tháng" in top["text"]

    report = run(notes.delete("notes", "guide.md", purge=True))
    assert report["purged"] and report["lexical_deleted"] > 0
    assert run(notes.search("notes", "moc an toan"))["hits"] == []
    assert run(notes.gc("notes"))["stale"] == 0
    assert notes.verify("notes").ok


def test_a_collection_with_no_index_is_refused_before_a_run(kbx, tmp_path):
    kbx.create_collection("bare", CollectionSpec())
    (tmp_path / "a.md").write_text("# A\n\ntext\n", encoding="utf-8")
    with pytest.raises(CatalogError, match="no index"):
        run(kbx.add("bare", str(tmp_path / "a.md"), key="a.md"))
