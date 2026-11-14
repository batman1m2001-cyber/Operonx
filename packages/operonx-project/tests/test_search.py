"""``search`` and ``read`` (ENGINEERING_ENV_PLAN E6): one ranked search over what a project knows."""

from __future__ import annotations

import json

import pytest

from operonx_project import search
from operonx_project.cli import main


@pytest.fixture
def repo(tmp_path):
    files = {
        "docs/brd/board.md": "# Board\n\n### C3 · retry the TTS · doing\nfiles: src/tts/ops.py\n\n"
        "### C4 · the dashboard tile · todo\nfiles: web/news.js\n",
        "docs/brd/knowledge.md": "# Knowledge\n\n## K1 · TTS retries twice · decision\n"
        "The owner decided: retry the TTS twice, not three times.\nwhere: src/tts/ops.py\n",
        "docs/RETRY_PLAN.md": "# Retry plan\n\n## Backoff\nA backoff releases its slot.\n\n"
        "```\n# not a heading: retry inside a fence\n```\n",
        ".operonx/guide/05-ops.md": "# Ops\n\nAn op is a function.\n",
        "AGENTS.md": "# Agents\n\nSearch before reading a whole doc.\n",
        "src/notes.md": "# Not read\nretry retry retry\n",
    }
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    return tmp_path


def test_search_ranks_sections_with_their_place(repo):
    hits = search.search(repo, "retry TTS")
    top = {(h.path, h.line): h for h in hits[:2]}
    assert set(top) == {("docs/brd/knowledge.md", 3), ("docs/brd/board.md", 3)}  # "retries" is "retry"
    k1 = top[("docs/brd/knowledge.md", 3)]
    assert k1.heading == "K1 · TTS retries twice · decision"
    assert "retry the TTS twice, not three times" in k1.lines[0]
    assert all(h.path != "src/notes.md" for h in hits)  # only where the project keeps what it knows
    assert search.search(repo, "the of") == [] and search.search(repo, "zebra") == []


def test_a_heading_inside_a_code_fence_is_text(repo):
    found = list(search.sections(repo / "docs/RETRY_PLAN.md"))
    assert [s[2] for s in found] == ["Retry plan", "Backoff"]


def test_read_one_section_or_the_file(repo):
    assert search.read(repo, "docs/brd/board.md", "C3") == ("### C3 · retry the TTS · doing\nfiles: src/tts/ops.py")
    assert search.read(repo, "docs/RETRY_PLAN.md", "Retry plan").endswith("```")  # sub-sections come along
    assert search.read(repo, "AGENTS.md").startswith("# Agents")
    with pytest.raises(KeyError):
        search.read(repo, "docs/brd/board.md", "C9")
    with pytest.raises(KeyError):
        search.read(repo, "../outside.md")


def test_cli_search_and_read(repo, capsys):
    assert main(["search", "backoff", "slot", "--repo", str(repo)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("Backoff — docs/RETRY_PLAN.md:3\n  A backoff releases its slot.")
    assert main(["search", "dashboard", "--repo", str(repo), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["path"] == "docs/brd/board.md"
    assert main(["read", "docs/brd/knowledge.md", "K1", "--repo", str(repo)]) == 0
    assert "twice" in capsys.readouterr().out
    assert main(["read", "docs/brd/knowledge.md", "K9", "--repo", str(repo)]) == 1
