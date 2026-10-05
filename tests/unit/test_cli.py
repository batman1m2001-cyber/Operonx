import json
from pathlib import Path

from operonx_kb.cli import main

DOCS = Path(__file__).parents[1] / "golden" / "docs"


def test_cli_create_add_list_status_verify_delete(hub, tmp_path, capsys):
    res = str(hub.source_path)
    assert (
        main(
            [
                "--resources",
                res,
                "create",
                "notes",
                "--embedder",
                "fake_embedding:hash",
                "--store",
                "vector_store:kb",
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "--resources",
                res,
                "add",
                "notes",
                str(DOCS / "meeting_notes.txt"),
                str(DOCS / "quy_trinh_vi.html"),
            ]
        )
        == 0
    )
    assert main(["--resources", res, "add", "notes", str(DOCS / "meeting_notes.txt")]) == 0
    out = capsys.readouterr().out
    assert out.count("new ") == 2 and "skip" in out
    assert main(["--resources", res, "collections"]) == 0
    assert "notes" in capsys.readouterr().out
    assert main(["--resources", res, "list", "notes"]) == 0
    assert capsys.readouterr().out.count("active") == 2
    assert main(["--resources", res, "status", "notes"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["documents"] == 2 and status["ingest_log"] == {"new": 2, "skip": 1}
    assert main(["--resources", res, "verify", "notes"]) == 0
    key = str((DOCS / "meeting_notes.txt").resolve())
    assert main(["--resources", res, "delete", "notes", key, "--purge"]) == 0
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["purged"] is True
    assert main(["--resources", res, "gc", "notes"]) == 0


def test_cli_reports_a_failed_ingest_with_exit_1(hub, tmp_path, capsys):
    res = str(hub.source_path)
    main(
        [
            "--resources",
            res,
            "create",
            "notes",
            "--embedder",
            "fake_embedding:hash",
            "--store",
            "vector_store:kb",
        ]
    )
    bad = tmp_path / "x.docx"
    bad.write_bytes(b"nope")
    assert main(["--resources", res, "add", "notes", str(bad)]) == 1
    assert "not a DOCX" in capsys.readouterr().err


def test_cli_query_and_eval_on_a_lexical_collection(hub, tmp_path, capsys):
    res = str(hub.source_path)
    assert main(["--resources", res, "create", "notes", "--embedder", "fake_embedding:hash",
                 "--store", "vector_store:kb", "--lexical", "kb_lexical:main", "--analyzer", "vi"]) == 0  # fmt: skip
    path = DOCS / "quy_trinh_vi.html"
    assert main(["--resources", res, "add", "notes", str(path)]) == 0
    capsys.readouterr()
    assert main(["--resources", res, "query", "notes", "nghỉ phép", "--mode", "lexical"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("  1  ") and "quy_trinh_vi.html" in out and "phép" in out
    assert main(["--resources", res, "query", "notes", "nghỉ phép", "--mode", "lexical",
                 "--tag", "nope", "--json"]) == 0  # fmt: skip
    assert json.loads(capsys.readouterr().out)["hits"] == []
    assert main(["--resources", res, "query", "notes", "x", "--mode", "lexical",
                 "--filter", '{"fields": {"dept": "hr"}}']) == 1  # fmt: skip
    assert "not filterable" in capsys.readouterr().err
    dataset = tmp_path / "cases.jsonl"
    dataset.write_text(json.dumps({"id": "a", "input": {"query": "nghỉ phép", "collection": "notes"},
                                   "expected": {"relevant": [{"doc_key": str(path.resolve()),
                                                              "quote": "nghỉ phép"}]}}) + "\n",
                       encoding="utf-8")  # fmt: skip
    assert main(["--resources", res, "eval", "notes", str(dataset), "--mode", "lexical"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["means"]["recall@20"] == 1.0


def test_cli_create_with_enrichment_stores_the_specs(hub, capsys):
    from operonx_kb import KnowledgeBase

    res = str(hub.source_path)
    assert main(["--resources", res, "create", "manuals", "--embedder", "fake_embedding:hash",
                 "--contextual-llm", "llm:gpt-4o-mini", "--tree-llm", "gpt-4o-mini"]) == 0  # fmt: skip
    spec = KnowledgeBase().collection("manuals").spec
    assert spec.contextual.llm == "gpt-4o-mini" and spec.tree.llm == "gpt-4o-mini"


def test_cli_create_with_the_graph_stores_its_spec(hub):
    from operonx_kb import GraphSpec, KnowledgeBase

    res = str(hub.source_path)
    assert main(["--resources", res, "create", "wiki", "--embedder", "fake_embedding:hash",
                 "--graph"]) == 0  # fmt: skip
    assert KnowledgeBase().collection("wiki").spec.graph == GraphSpec()
