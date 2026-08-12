"""The tools, called the way dispatch calls them.

Each ``@tool`` is an op factory, so the underlying coroutine is reached
through ``__wrapped__``. These assert on what the *model* would receive,
including the error text — a tool's refusal message is the only thing it
has to recover from, so a message that does not say what to do next is a
defect.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from operonx_code.shell import PersistentShell, set_shell
from operonx_code.tools import register_all
from operonx_code.tools.fs import edit_file, read_file, write_file
from operonx_code.tools.search import glob_files, grep_files
from operonx_code.tools.shell_tool import run_bash
from operonx_code.workspace import StaleRead, Workspace, WorkspaceError, set_workspace

from operonx.agents import TOOL_REGISTRY, clear_registry

pytestmark = pytest.mark.unit

read = read_file.__wrapped__
write = write_file.__wrapped__
edit = edit_file.__wrapped__
glob = glob_files.__wrapped__
grep = grep_files.__wrapped__
bash = run_bash.__wrapped__


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("def run():\n    return 42\n")
    (tmp_path / "src" / "util.py").write_text("SECRET = 'x'\ndef helper():\n    pass\n")
    (tmp_path / "README.md").write_text("# demo\n")
    set_workspace(Workspace(root=tmp_path))
    return tmp_path


class TestRead:
    async def test_returns_numbered_lines(self, project):
        """Every later reference — an edit, a traceback — is by line. Bare
        text makes the model count, and it counts wrong."""
        out = await read(path="src/main.py")
        assert out["content"].startswith("1\tdef run():")
        assert "2\t    return 42" in out["content"]
        assert out["lines"] == 2

    async def test_offset_and_limit(self, project):
        out = await read(path="src/util.py", offset=2, limit=1)
        assert out["content"] == "2\tdef helper():"
        assert out["truncated"] is True

    async def test_a_directory_is_refused_with_a_pointer(self, project):
        with pytest.raises(WorkspaceError, match="use glob"):
            await read(path="src")

    async def test_a_missing_file_says_so(self, project):
        with pytest.raises(WorkspaceError, match="does not exist"):
            await read(path="nope.py")

    async def test_escaping_the_root_is_refused(self, project):
        with pytest.raises(WorkspaceError):
            await read(path="../../etc/passwd")


class TestEdit:
    async def test_requires_a_read_first(self, project):
        """The invariant that stops a model editing from memory."""
        with pytest.raises(StaleRead, match="has not been read"):
            await edit(path="src/main.py", old="42", new="43")

    async def test_edits_after_a_read(self, project):
        await read(path="src/main.py")
        out = await edit(path="src/main.py", old="42", new="43")
        assert out["replaced"] == 1
        assert "43" in (project / "src" / "main.py").read_text()

    async def test_refuses_after_the_file_changed(self, project):
        await read(path="src/main.py")
        (project / "src" / "main.py").write_text("something else\n")
        with pytest.raises(StaleRead, match="changed on disk"):
            await edit(path="src/main.py", old="something", new="x")

    async def test_an_ambiguous_match_is_refused(self, project):
        """Replacing the first of several is a coin flip the caller did
        not know they were tossing."""
        (project / "dup.py").write_text("a = 1\na = 1\n")
        await read(path="dup.py")
        with pytest.raises(WorkspaceError, match="appears 2 times"):
            await edit(path="dup.py", old="a = 1", new="a = 2")

    async def test_replace_all_handles_the_ambiguous_case(self, project):
        (project / "dup.py").write_text("a = 1\na = 1\n")
        await read(path="dup.py")
        out = await edit(path="dup.py", old="a = 1", new="a = 2", replace_all=True)
        assert out["replaced"] == 2

    async def test_a_missing_string_says_it_must_match_exactly(self, project):
        await read(path="src/main.py")
        with pytest.raises(WorkspaceError, match="exactly"):
            await edit(path="src/main.py", old="nonexistent", new="x")

    async def test_a_noop_edit_is_refused(self, project):
        await read(path="src/main.py")
        with pytest.raises(WorkspaceError, match="nothing to do"):
            await edit(path="src/main.py", old="42", new="42")

    async def test_a_second_edit_works_without_re_reading(self, project):
        """The write updates the ledger; forcing a re-read after every
        edit would double the turns for no safety."""
        await read(path="src/main.py")
        await edit(path="src/main.py", old="42", new="43")
        await edit(path="src/main.py", old="43", new="44")
        assert "44" in (project / "src" / "main.py").read_text()


class TestWrite:
    async def test_creates_a_new_file(self, project):
        out = await write(path="new/thing.py", content="x = 1\n")
        assert out["created"] is True
        assert (project / "new" / "thing.py").read_text() == "x = 1\n"

    async def test_overwriting_an_unread_file_is_refused(self, project):
        """`write` is a blind edit done wholesale."""
        with pytest.raises(WorkspaceError, match="has not been read"):
            await write(path="README.md", content="gone")

    async def test_overwriting_after_a_read_is_allowed(self, project):
        await read(path="README.md")
        out = await write(path="README.md", content="# new\n")
        assert out["created"] is False

    async def test_escaping_the_root_is_refused(self, project):
        with pytest.raises(WorkspaceError):
            await write(path="../evil.py", content="x")


class TestSearch:
    async def test_glob_finds_by_pattern(self, project):
        out = await glob(pattern="*.py")
        assert sorted(out["files"]) == ["src/main.py", "src/util.py"]

    @pytest.mark.parametrize(
        "pattern",
        [
            pytest.param("*.py", id="bare"),
            pytest.param("**/*.py", id="recursive-wildcard"),
            pytest.param("src/*.py", id="explicit-dir"),
        ],
    )
    async def test_the_usual_patterns_all_find_the_files(self, project, pattern):
        """`**/*.py` returned nothing at all: fnmatch has no concept of a
        path separator, so the pattern required a literal `/` and could
        never match a root-level file. A live agent read `{"count": 0}` as
        "no Python here" and burned two turns before falling back to ls."""
        out = await glob(pattern=pattern)
        assert "src/main.py" in out["files"], f"{pattern} found {out['files']}"

    async def test_a_root_level_file_matches_a_recursive_pattern(self, project):
        out = await glob(pattern="**/README.md")
        assert out["files"] == ["README.md"]

    async def test_no_matches_is_still_an_empty_list(self, project):
        out = await glob(pattern="*.rs")
        assert out["files"] == [] and out["count"] == 0

    async def test_glob_skips_noise_directories(self, project):
        (project / "node_modules").mkdir()
        (project / "node_modules" / "junk.py").write_text("x")
        out = await glob(pattern="*.py")
        assert not any("node_modules" in f for f in out["files"])

    async def test_grep_finds_content(self, project):
        out = await grep(pattern="def helper")
        assert out["count"] == 1
        assert "src/util.py" in out["matches"][0]

    async def test_grep_paths_are_relative_to_the_root(self, project):
        """Absolute paths leak the host layout and waste context."""
        out = await grep(pattern="def ")
        assert all(not m.startswith("/") for m in out["matches"])

    async def test_no_matches_is_an_answer_not_an_error(self, project):
        out = await grep(pattern="zzzz_not_here")
        assert out["count"] == 0
        assert out["matches"] == []

    async def test_a_bad_regex_says_it_is_bad(self, project):
        import shutil

        if shutil.which("rg"):
            pytest.skip("ripgrep reports invalid patterns on its own path")
        with pytest.raises(WorkspaceError, match="invalid regular expression"):
            await grep(pattern="([unclosed")


class TestBash:
    @pytest.fixture(autouse=True)
    def _shell(self, project):
        set_shell(PersistentShell(cwd=project))

    async def test_runs_and_reports_the_exit_code(self, project):
        out = await bash(command="echo hi")
        assert out["output"] == "hi"
        assert out["exit_code"] == 0
        assert out["timed_out"] is False

    async def test_a_timeout_is_returned_not_raised(self, project):
        """A timeout is information the model can act on — split the work,
        redirect to a file. An exception reaches it as an opaque error."""
        out = await bash(command="sleep 5", timeout=0.4)
        assert out["timed_out"] is True
        assert out["exit_code"] == 124
        assert "cd/export" in out["output"]


class TestRegistration:
    def test_every_tool_registers(self):
        clear_registry()
        register_all()
        assert set(TOOL_REGISTRY) == {
            "read",
            "write",
            "edit",
            "glob",
            "grep",
            "bash",
            "webfetch",
        }
        clear_registry()
        register_all()

    def test_register_all_is_idempotent(self):
        register_all()
        register_all()
        assert "read" in TOOL_REGISTRY

    def test_mutating_tools_are_destructive(self):
        register_all()
        for name in ("bash", "write", "edit"):
            assert TOOL_REGISTRY[name]._tool_meta["destructive"] is True, name

    def test_inspecting_tools_are_readonly(self):
        register_all()
        for name in ("read", "glob", "grep", "webfetch"):
            meta = TOOL_REGISTRY[name]._tool_meta
            assert meta["readonly"] is True and meta["destructive"] is False, name
