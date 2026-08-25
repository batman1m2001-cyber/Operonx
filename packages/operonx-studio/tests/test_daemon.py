"""Local daemon.

The claim under test is that extraction happens in a **fresh subprocess**:
re-importing in-process would return the cached module and serve stale
structure while the page claimed to be live.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from operonx_studio.daemon import ProjectWatcher, build_app, page_for

pytestmark = pytest.mark.unit

WORKFLOW = """
from operonx.core import END, PARENT, START, graph, op

@op
def first(x: int):
    return {"y": x + 1}

@graph
def flow(x):
    a = first(x=x)
    a["y"] >> PARENT["y"]
    START >> a >> END
"""

MANIFEST = '[project]\nname="live"\n[[graph]]\nname="flow"\nentry="{mod}:flow"\n'


@pytest.fixture
def project(tmp_path, request):
    """A throwaway project with a module name unique to this test."""
    mod = f"live_{abs(hash(request.node.name)) % 100000}"
    (tmp_path / f"{mod}.py").write_text(WORKFLOW, encoding="utf-8")
    (tmp_path / "operonx.toml").write_text(MANIFEST.format(mod=mod), encoding="utf-8")
    return tmp_path, mod


class TestWatchedFiles:
    def test_tracks_source_and_config(self, project):
        root, mod = project
        (root / "resources.yaml").write_text("llm:x: {}\n", encoding="utf-8")
        (root / ".env").write_text("K=1\n", encoding="utf-8")
        names = {p.name for p in ProjectWatcher(root=root).watched_files()}
        assert {f"{mod}.py", "operonx.toml", "resources.yaml", ".env"} <= names

    def test_ignores_virtualenvs_and_caches(self, project):
        root, _ = project
        for junk in (".venv/lib/x.py", "__pycache__/y.py", ".git/z.yaml"):
            path = root / junk
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("x = 1\n", encoding="utf-8")
        found = {str(p.relative_to(root)) for p in ProjectWatcher(root=root).watched_files()}
        assert not any(part in f for f in found for part in (".venv", "__pycache__", ".git"))


class TestChangeDetection:
    def test_first_call_reports_change_then_settles(self, project):
        root, _ = project
        watcher = ProjectWatcher(root=root)
        assert watcher.changed() is True
        assert watcher.changed() is False

    def test_edit_is_detected(self, project):
        root, mod = project
        watcher = ProjectWatcher(root=root)
        watcher.changed()
        (root / f"{mod}.py").write_text(WORKFLOW + "\n# touched\n", encoding="utf-8")
        assert watcher.changed() is True

    def test_new_file_is_detected(self, project):
        root, _ = project
        watcher = ProjectWatcher(root=root)
        watcher.changed()
        (root / "extra.py").write_text("x = 1\n", encoding="utf-8")
        assert watcher.changed() is True


class TestExtraction:
    def test_extracts_in_a_subprocess(self, project):
        root, _ = project
        result = ProjectWatcher(root=root).extract()
        assert result.ok, result.error
        assert [g["name"] for g in result.ir["graphs"]] == ["flow"]

    def test_edits_are_picked_up_across_extractions(self, project):
        """In-process re-import would hand back the cached module."""
        root, mod = project
        watcher = ProjectWatcher(root=root)
        before = watcher.extract()
        assert len(before.ir["graphs"][0]["nodes"]) == 1

        (root / f"{mod}.py").write_text(
            WORKFLOW.replace(
                '    a = first(x=x)\n    a["y"] >> PARENT["y"]\n    START >> a >> END',
                '    a = first(x=x)\n    b = first(x=a["y"])\n'
                '    b["y"] >> PARENT["y"]\n    START >> a >> b >> END',
            ),
            encoding="utf-8",
        )
        after = watcher.extract()
        assert len(after.ir["graphs"][0]["nodes"]) == 2

    def test_broken_project_reports_instead_of_raising(self, project):
        root, mod = project
        (root / f"{mod}.py").write_text("this is not python ((((\n", encoding="utf-8")
        result = ProjectWatcher(root=root).extract()
        assert not result.ok and result.error

    def test_missing_manifest_reports(self, tmp_path):
        result = ProjectWatcher(root=tmp_path).extract()
        assert not result.ok and "operonx.toml" in (result.error or "")

    def test_refresh_reuses_the_last_result_when_nothing_moved(self, project):
        root, _ = project
        watcher = ProjectWatcher(root=root)
        first = watcher.refresh()
        assert watcher.refresh().stamp == first.stamp


class TestPage:
    def test_success_page_carries_the_graph_and_reload_hook(self, project):
        root, _ = project
        watcher = ProjectWatcher(root=root)
        page = page_for(watcher, watcher.extract())
        assert "__OPERONX_STAMP__" in page and "api/stamp" in page

    def test_failure_renders_the_error_not_a_dead_server(self, project):
        root, mod = project
        (root / f"{mod}.py").write_text("boom ((((\n", encoding="utf-8")
        watcher = ProjectWatcher(root=root)
        page = page_for(watcher, watcher.extract())
        assert "Could not build" in page
        assert "api/stamp" in page, "must keep polling so it recovers on its own"


class TestRoutes:
    @pytest.fixture
    def client(self, project):
        fastapi_testclient = pytest.importorskip("fastapi.testclient")
        root, _ = project
        return fastapi_testclient.TestClient(build_app(root))

    def test_index_serves_the_page(self, client):
        response = client.get("/")
        assert response.status_code == 200 and "operonx studio" in response.text

    def test_stamp_endpoint(self, client):
        body = client.get("/api/stamp").json()
        assert body["ok"] is True and body["stamp"] > 0

    def test_ir_endpoint(self, client):
        body = client.get("/api/ir").json()
        assert body["ir_version"] == 1

    def test_ir_reports_failure_with_503(self, project):
        fastapi_testclient = pytest.importorskip("fastapi.testclient")
        root, mod = project
        (root / f"{mod}.py").write_text("nope ((((\n", encoding="utf-8")
        client = fastapi_testclient.TestClient(build_app(root))
        response = client.get("/api/ir")
        assert response.status_code == 503 and "error" in response.json()


class TestStaticStaysInert:
    def test_the_written_file_has_no_reload_polling(self, project):
        """A shared HTML file must not poll a daemon that is not there."""
        from operonx_studio.render import render_html

        root, _ = project
        result = ProjectWatcher(root=root).extract()
        assert "api/stamp" not in render_html(result.ir)


class TestInterpreterSelection:
    """One studio installation must serve projects whose deps it lacks."""

    def test_prefers_the_project_virtualenv(self, project):
        root, _ = project
        venv_python = root / ".venv" / "bin" / "python"
        venv_python.parent.mkdir(parents=True)
        venv_python.write_text("", encoding="utf-8")
        assert ProjectWatcher(root=root).interpreter() == str(venv_python)

    def test_falls_back_to_our_own(self, project):
        import sys

        root, _ = project
        assert ProjectWatcher(root=root).interpreter() == sys.executable

    def test_only_the_toolkit_is_put_on_the_child_path(self, project):
        """Injecting our whole sys.path shadowed the child's own packages —
        and could break its import of the standard library."""
        import operonx_project

        root, _ = project
        env = ProjectWatcher(root=root)._child_env()
        toolkit = str(Path(operonx_project.__file__).resolve().parent.parent)
        entries = env["PYTHONPATH"].split(os.pathsep)
        assert entries[0] == toolkit
        assert not any("site-packages" in e for e in entries), env["PYTHONPATH"]

    def test_the_injected_path_is_absolute(self, project):
        """The child runs with cwd set to the project, so relative is wrong."""
        root, _ = project
        for entry in ProjectWatcher(root=root)._child_env()["PYTHONPATH"].split(os.pathsep):
            assert Path(entry).is_absolute()


class TestEditEndpoint:
    @pytest.fixture
    def client(self, project):
        fastapi_testclient = pytest.importorskip("fastapi.testclient")
        root, _ = project
        return fastapi_testclient.TestClient(build_app(root)), root

    def test_dry_run_is_the_default(self, client):
        """A request that forgets the flag must preview, never write."""
        api, root = client
        source = next(root.glob("live_*.py"))
        before = source.read_text(encoding="utf-8")
        body = api.post(
            "/api/edit", json={"graph": "flow", "action": "rename", "old": "a", "new": "renamed"}
        ).json()
        assert body["changed"] is True and body["applied"] is False
        assert "+" in body["diff"] and "renamed" in body["diff"]
        assert source.read_text(encoding="utf-8") == before

    def test_applying_writes_the_file(self, client):
        api, root = client
        source = next(root.glob("live_*.py"))
        body = api.post(
            "/api/edit",
            json={
                "graph": "flow",
                "action": "rename",
                "old": "a",
                "new": "renamed",
                "dry_run": False,
            },
        ).json()
        assert body["applied"] is True
        assert "renamed = first" in source.read_text(encoding="utf-8")

    def test_the_new_structure_is_visible_immediately_after(self, client):
        api, _ = client
        api.post(
            "/api/edit",
            json={
                "graph": "flow",
                "action": "rename",
                "old": "a",
                "new": "renamed",
                "dry_run": False,
            },
        )
        names = [n["name"] for n in api.get("/api/ir").json()["graphs"][0]["nodes"]]
        assert names == ["renamed"]

    def test_a_no_op_reports_no_change(self, client):
        api, _ = client
        body = api.post(
            "/api/edit", json={"graph": "flow", "action": "rename", "old": "a", "new": "a"}
        ).json()
        assert body["changed"] is False and body["diff"] == ""

    def test_missing_arguments(self, client):
        api, _ = client
        assert api.post("/api/edit", json={"graph": "flow"}).status_code == 400

    def test_unknown_action_is_rejected(self, client):
        api, _ = client
        response = api.post("/api/edit", json={"graph": "flow", "action": "explode"})
        assert response.status_code == 400 and "unknown action" in response.json()["error"]

    def test_bad_arguments_for_a_known_action(self, client):
        api, _ = client
        response = api.post("/api/edit", json={"graph": "flow", "action": "rename", "nonsense": 1})
        assert response.status_code == 400

    def test_an_inapplicable_edit_explains_itself(self, client):
        api, _ = client
        response = api.post(
            "/api/edit", json={"graph": "flow", "action": "rename", "old": "absent", "new": "x"}
        )
        assert response.status_code == 400 and "no name" in response.json()["error"]


class TestServedPageIsEditable:
    def test_served_page_sets_the_flag(self, project):
        root, _ = project
        watcher = ProjectWatcher(root=root)
        assert "__OPERONX_EDITABLE__ = true" in page_for(watcher, watcher.extract())

    def test_the_error_page_does_not_offer_editing(self, project):
        """Nothing to edit against when the project will not build."""
        root, mod = project
        (root / f"{mod}.py").write_text("boom ((((\n", encoding="utf-8")
        watcher = ProjectWatcher(root=root)
        assert "__OPERONX_EDITABLE__" not in page_for(watcher, watcher.extract())
