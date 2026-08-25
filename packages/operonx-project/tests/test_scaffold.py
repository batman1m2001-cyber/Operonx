"""Scaffolding.

The assertions that matter run the project's **own tools** against what was
generated. String checks would let the scaffold drift out of compliance
while still looking right; a generator that emits a project failing its own
linter is worse than no generator.
"""

from __future__ import annotations

import pytest
from operonx_project.lint import lint_path
from operonx_project.manifest import Manifest
from operonx_project.scaffold import OPERONX_PIN, ScaffoldError, scaffold

pytestmark = pytest.mark.unit


class TestLayout:
    def test_plain_project_files(self, tmp_path):
        names = {p.name for p in scaffold(tmp_path, "demo")}
        assert names == {"pyproject.toml", "operonx.toml", "workflow.py", "README.md"}

    def test_llm_project_adds_resources_and_env(self, tmp_path):
        names = {p.name for p in scaffold(tmp_path, "demo", with_llm=True)}
        assert {"resources.yaml", ".env.example"} <= names

    def test_no_requirements_txt(self, tmp_path):
        """uv and every tutorial example use pyproject.toml."""
        names = {p.name for p in scaffold(tmp_path, "demo")}
        assert "requirements.txt" not in names

    def test_name_defaults_to_the_directory(self, tmp_path):
        target = tmp_path / "my_project"
        scaffold(target)
        assert 'name = "my_project"' in (target / "operonx.toml").read_text(encoding="utf-8")

    def test_distribution_name_is_slugified(self, tmp_path):
        scaffold(tmp_path, "My Project")
        assert 'name = "my-project"' in (tmp_path / "pyproject.toml").read_text(encoding="utf-8")

    def test_pins_above_the_breaking_release(self, tmp_path):
        """1.0.0 was breaking; a lower floor could resolve to an older API."""
        scaffold(tmp_path, "demo")
        text = (tmp_path / "pyproject.toml").read_text(encoding="utf-8")
        assert f"operonx>={OPERONX_PIN}" in text
        assert tuple(int(p) for p in OPERONX_PIN.split(".")) >= (1, 0, 0)

    def test_llm_variant_takes_the_openai_extra(self, tmp_path):
        scaffold(tmp_path, "demo", with_llm=True)
        assert "operonx[openai]" in (tmp_path / "pyproject.toml").read_text(encoding="utf-8")


class TestNeverClobbers:
    def test_refuses_when_a_file_already_exists(self, tmp_path):
        (tmp_path / "workflow.py").write_text("mine\n", encoding="utf-8")
        with pytest.raises(ScaffoldError, match="refusing to overwrite"):
            scaffold(tmp_path, "demo")
        assert (tmp_path / "workflow.py").read_text(encoding="utf-8") == "mine\n"

    def test_names_what_is_in_the_way(self, tmp_path):
        (tmp_path / "operonx.toml").write_text("x\n", encoding="utf-8")
        with pytest.raises(ScaffoldError, match="operonx.toml"):
            scaffold(tmp_path, "demo")

    def test_creates_a_missing_directory(self, tmp_path):
        target = tmp_path / "deep" / "nested"
        scaffold(target, "demo")
        assert (target / "workflow.py").exists()


class TestItSatisfiesTheConventions:
    """Run the project's own checks over what was generated."""

    @pytest.mark.parametrize("with_llm", [False, True])
    def test_lints_without_a_single_finding(self, tmp_path, with_llm):
        scaffold(tmp_path, "demo", with_llm=with_llm)
        findings = lint_path(tmp_path)
        assert findings == [], [str(f) for f in findings]

    @pytest.mark.parametrize("with_llm", [False, True])
    def test_the_manifest_parses(self, tmp_path, with_llm):
        scaffold(tmp_path, "demo", with_llm=with_llm)
        manifest = Manifest.load(tmp_path)
        assert [g.name for g in manifest.graphs] == ["flow"]

    def test_the_env_contract_is_derived_from_resources(self, tmp_path):
        from operonx_project.extract import extract_resources

        scaffold(tmp_path, "demo", with_llm=True)
        resources = extract_resources(Manifest.load(tmp_path))
        assert resources["keys"] == ["llm:gpt-4o-mini"]
        assert resources["env"]["required"] == ["OPENAI_API_KEY"]

    def test_the_plain_variant_declares_no_resources(self, tmp_path):
        from operonx_project.extract import extract_resources

        scaffold(tmp_path, "demo")
        assert extract_resources(Manifest.load(tmp_path))["keys"] == []


class TestItBuildsAndExtracts:
    """One end-to-end check: the scaffold always names its module
    ``workflow``, and one process handles one project, so a second build in
    the same interpreter would hit the collision guard by design."""

    def test_builds_offline_and_extracts(self, tmp_path):
        from operonx_project.buildcheck import check_build
        from operonx_project.extract import extract_project

        scaffold(tmp_path, "demo")
        manifest = Manifest.load(tmp_path)

        (report,) = check_build(manifest)
        assert report.clean, report.error
        assert not report.slow

        graph = extract_project(manifest)["graphs"][0]
        assert [n["name"] for n in graph["nodes"]] == ["clean", "report"]
        assert [(e["from"], e["to"]) for e in graph["edges"]] == [("clean", "report")]
