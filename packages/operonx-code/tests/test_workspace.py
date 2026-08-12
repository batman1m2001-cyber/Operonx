"""Workspace containment and the read-before-edit ledger.

Both invariants exist because their failures are silent: a path escape
edits a file nobody was looking at, and a blind edit applies cleanly to
the file the model imagined.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from operonx_code.workspace import (
    OutsideWorkspace,
    StaleRead,
    Workspace,
    WorkspaceError,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1\n")
    return Workspace(root=tmp_path)


class TestContainment:
    def test_relative_path_resolves_under_the_root(self, ws):
        assert ws.resolve("src/a.py") == ws.root / "src" / "a.py"

    def test_absolute_path_inside_is_allowed(self, ws):
        assert ws.resolve(str(ws.root / "src" / "a.py")).name == "a.py"

    def test_the_root_itself_is_inside(self, ws):
        assert ws.resolve(".") == ws.root

    @pytest.mark.parametrize(
        "escape",
        [
            pytest.param("../outside.txt", id="parent"),
            pytest.param("src/../../outside.txt", id="through-a-subdir"),
            pytest.param("/etc/passwd", id="absolute"),
        ],
    )
    def test_escapes_are_refused(self, ws, escape):
        with pytest.raises(OutsideWorkspace):
            ws.resolve(escape)

    def test_a_symlink_out_is_refused(self, ws, tmp_path):
        """The whole attack: a link *inside* the root pointing outside.
        A lexical prefix check passes it, which is why resolve() calls
        realpath before comparing."""
        secret = tmp_path.parent / "secret.txt"
        secret.write_text("token")
        os.symlink(secret, ws.root / "link.txt")
        with pytest.raises(OutsideWorkspace):
            ws.resolve("link.txt")

    def test_a_symlink_within_the_root_is_fine(self, ws):
        os.symlink(ws.root / "src" / "a.py", ws.root / "alias.py")
        assert ws.resolve("alias.py").name == "a.py"

    def test_empty_path_is_refused(self, ws):
        with pytest.raises(WorkspaceError):
            ws.resolve("   ")

    def test_a_missing_root_is_refused_at_construction(self, tmp_path):
        with pytest.raises(WorkspaceError):
            Workspace(root=tmp_path / "nope")


class TestReadLedger:
    def test_an_unread_file_cannot_be_edited(self, ws):
        with pytest.raises(StaleRead, match="has not been read"):
            ws.check_fresh(ws.resolve("src/a.py"))

    def test_a_read_file_passes(self, ws):
        path = ws.resolve("src/a.py")
        ws.read_text(path)
        ws.check_fresh(path)

    def test_a_file_changed_since_the_read_is_stale(self, ws):
        """Content, not mtime — an edit inside the same clock tick is
        still an edit, and mtime resolution is filesystem-dependent."""
        path = ws.resolve("src/a.py")
        ws.read_text(path)
        path.write_text("x = 2\n")
        with pytest.raises(StaleRead, match="changed on disk"):
            ws.check_fresh(path)

    def test_a_deleted_file_is_reported_as_such(self, ws):
        path = ws.resolve("src/a.py")
        ws.read_text(path)
        path.unlink()
        with pytest.raises(StaleRead, match="no longer exists"):
            ws.check_fresh(path)

    def test_rewriting_the_same_content_is_not_stale(self, ws):
        path = ws.resolve("src/a.py")
        ws.read_text(path)
        path.write_text("x = 1\n")
        ws.check_fresh(path)


class TestReadText:
    def test_binary_is_refused(self, ws):
        (ws.root / "blob.bin").write_bytes(b"\x00\x01\x02")
        with pytest.raises(WorkspaceError, match="binary"):
            ws.read_text(ws.resolve("blob.bin"))

    def test_a_large_file_is_truncated_not_refused(self, tmp_path):
        """A coding agent asking for a 40MB log wants the head of it."""
        ws = Workspace(root=tmp_path, max_read_bytes=100)
        (tmp_path / "big.txt").write_text("y" * 5000)
        text, truncated = ws.read_text(ws.resolve("big.txt"))
        assert truncated is True
        assert len(text) == 100

    def test_the_ledger_records_the_whole_file_not_the_truncation(self, tmp_path):
        """Otherwise editing a truncated read always looks stale."""
        ws = Workspace(root=tmp_path, max_read_bytes=10)
        target = tmp_path / "big.txt"
        target.write_text("y" * 500)
        ws.read_text(ws.resolve("big.txt"))
        ws.check_fresh(ws.resolve("big.txt"))
