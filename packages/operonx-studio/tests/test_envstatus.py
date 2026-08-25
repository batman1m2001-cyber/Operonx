"""Env presence — names and locations only, never values."""

from __future__ import annotations

import pytest
from operonx_studio.envstatus import dotenv_names, env_status

pytestmark = pytest.mark.unit


class TestDotenvNames:
    def test_reads_names_only(self, tmp_path):
        (tmp_path / ".env").write_text(
            "A=1\nexport B=2\n# C=3\n\n  D  =4\nnot an assignment\n", encoding="utf-8"
        )
        assert dotenv_names(tmp_path / ".env") == {"A", "B", "D"}

    def test_commented_lines_do_not_count_as_set(self, tmp_path):
        (tmp_path / ".env").write_text("# SECRET=x\n", encoding="utf-8")
        assert dotenv_names(tmp_path / ".env") == set()

    def test_absent_file(self, tmp_path):
        assert dotenv_names(tmp_path / ".env") == set()


class TestStatus:
    def test_distinguishes_environment_from_dotenv(self, tmp_path, monkeypatch):
        """The two fail differently — a name in .env is useless if nothing loads it."""
        (tmp_path / ".env").write_text("FROM_FILE=x\n", encoding="utf-8")
        monkeypatch.setenv("FROM_ENV", "y")
        out = env_status(tmp_path, ["FROM_FILE", "FROM_ENV", "NOWHERE"], [])
        assert out["FROM_FILE"] == {
            "set": True,
            "in_environment": False,
            "in_dotenv": True,
            "in_example": False,
        }
        assert out["FROM_ENV"]["in_environment"] and not out["FROM_ENV"]["in_dotenv"]
        assert out["NOWHERE"]["set"] is False

    def test_example_file_alone_does_not_count_as_set(self, tmp_path, monkeypatch):
        """.env.example documents the contract; it does not satisfy it."""
        (tmp_path / ".env.example").write_text("KEY=replace-me\n", encoding="utf-8")
        monkeypatch.delenv("KEY", raising=False)
        out = env_status(tmp_path, ["KEY"], [])
        assert out["KEY"]["set"] is False and out["KEY"]["in_example"] is True

    def test_optional_variables_are_reported_too(self, tmp_path):
        out = env_status(tmp_path, [], ["LOG_LEVEL"])
        assert "LOG_LEVEL" in out
