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
