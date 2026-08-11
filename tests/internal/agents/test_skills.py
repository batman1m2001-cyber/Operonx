"""Skills — loading, matching, and where they get injected.

The characteristic failure is silence. A skill with bad frontmatter, a
duplicate name, or triggers that never fire is indistinguishable at
runtime from a skill that does not exist — the agent simply does not know
the procedure, and nobody finds out why. So loading warns loudly and
these tests assert on the skipping rules as much as the happy path.
"""

from __future__ import annotations

import pytest

from operonx.agents.skills import (
    Skill,
    inject_skills,
    load_skills,
    match_skills,
    render_skills,
    skill_summaries,
)

pytestmark = pytest.mark.unit

inject = inject_skills.__wrapped__


def write_skill(
    root, name, *, description="does a thing", triggers=None, always=False, body="Steps here."
):
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    lines = ["---", f"name: {name}", f"description: {description}"]
    if triggers:
        lines.append("triggers:")
        lines.extend(f"  - {t}" for t in triggers)
    if always:
        lines.append("always: true")
    lines += ["---", "", body]
    (directory / "SKILL.md").write_text("\n".join(lines), encoding="utf-8")
    return directory / "SKILL.md"


@pytest.fixture
def skill_root(tmp_path):
    write_skill(
        tmp_path, "deploy", description="How to deploy.", triggers=["deploy", "release", "ship"]
    )
    write_skill(
        tmp_path, "debug", description="How to debug.", triggers=["debug", "traceback", "error"]
    )
    return tmp_path


class TestLoading:
    def test_loads_every_skill(self, skill_root):
        assert {s.name for s in load_skills(skill_root)} == {"deploy", "debug"}

    def test_body_and_triggers_survive(self, skill_root):
        deploy = next(s for s in load_skills(skill_root) if s.name == "deploy")
        assert deploy.body == "Steps here."
        assert "release" in deploy.triggers

    def test_missing_directory_returns_empty(self, tmp_path):
        assert load_skills(tmp_path / "nope") == []

    def test_no_frontmatter_is_skipped(self, tmp_path):
        d = tmp_path / "bare"
        d.mkdir()
        (d / "SKILL.md").write_text("just markdown, no frontmatter", encoding="utf-8")
        assert load_skills(tmp_path) == []

    def test_malformed_yaml_is_skipped_not_raised(self, tmp_path):
        """One bad file must not stop an agent starting."""
        d = tmp_path / "broken"
        d.mkdir()
        (d / "SKILL.md").write_text("---\nname: [unclosed\n---\nbody", encoding="utf-8")
        assert load_skills(tmp_path) == []

    def test_missing_description_is_skipped(self, tmp_path):
        d = tmp_path / "nameless"
        d.mkdir()
        (d / "SKILL.md").write_text("---\nname: x\n---\nbody", encoding="utf-8")
        assert load_skills(tmp_path) == []

    def test_duplicate_names_keep_only_the_first(self, tmp_path):
        """Two skills answering to one name means one is unreachable, and
        which one would depend on directory order."""
        write_skill(tmp_path / "a", "dup", description="first")
        write_skill(tmp_path / "b", "dup", description="second")
        loaded = load_skills(tmp_path)
        assert len(loaded) == 1

    def test_a_bad_file_does_not_hide_the_good_ones(self, tmp_path):
        write_skill(tmp_path, "good", description="fine")
        bad = tmp_path / "bad"
        bad.mkdir()
        (bad / "SKILL.md").write_text("no frontmatter", encoding="utf-8")
        assert [s.name for s in load_skills(tmp_path)] == ["good"]


class TestMatching:
    def test_matches_on_a_trigger(self, skill_root):
        skills = load_skills(skill_root)
        assert [s.name for s in match_skills("how do I ship this", skills)] == ["deploy"]

    def test_no_match_returns_nothing(self, skill_root):
        assert match_skills("what is the weather", load_skills(skill_root)) == []

    def test_respects_the_limit(self, skill_root):
        skills = load_skills(skill_root)
        assert len(match_skills("deploy debug", skills, limit=1)) == 1

    def test_always_skills_bypass_matching(self, tmp_path):
        write_skill(tmp_path, "house-style", description="Always apply.", always=True)
        skills = load_skills(tmp_path)
        assert [s.name for s in match_skills("totally unrelated", skills)] == ["house-style"]

    def test_always_skills_do_not_consume_the_limit(self, tmp_path):
        """Dropping a skill declared unconditional because three keyword
        matches outranked it would make the flag a lie."""
        write_skill(tmp_path, "always-on", description="Always.", always=True)
        write_skill(tmp_path, "deploy", description="Deploy.", triggers=["deploy"])
        names = [s.name for s in match_skills("deploy", load_skills(tmp_path), limit=1)]
        assert set(names) == {"always-on", "deploy"}

    def test_description_is_the_fallback_vocabulary(self, tmp_path):
        """A skill with no triggers should still have a chance of
        matching rather than silently never firing."""
        write_skill(tmp_path, "kubernetes", description="Rolling restarts in kubernetes")
        skills = load_skills(tmp_path)
        assert [s.name for s in match_skills("kubernetes restarts", skills)] == ["kubernetes"]

    def test_single_characters_do_not_match_everything(self, skill_root):
        assert match_skills("a I", load_skills(skill_root)) == []


class TestRendering:
    def test_none_when_nothing_selected(self):
        assert render_skills([]) is None

    def test_includes_name_description_and_body(self):
        block = render_skills([Skill(name="x", description="d", body="b")])
        assert "name='x'" in block and "d" in block and "b" in block

    def test_wraps_in_a_single_block(self):
        block = render_skills([Skill("a", "d", "b"), Skill("c", "d", "b")])
        assert block.count("<skills>") == 1


class TestInjection:
    def test_injects_as_a_user_message(self, skill_root):
        """Skills change per query; the system prompt is the cached
        prefix, so they must not go there."""
        out = inject(query="ship it", skills=load_skills(skill_root))
        assert out["message"]["role"] == "user"
        assert "deploy" in out["message"]["content"]

    def test_no_match_yields_no_message(self, skill_root):
        out = inject(query="unrelated question", skills=load_skills(skill_root))
        assert out["message"] is None
        assert out["count"] == 0

    def test_reports_which_skills_fired(self, skill_root):
        """A skill that fails to match is invisible, so the selection has
        to be inspectable."""
        out = inject(query="traceback in the logs", skills=load_skills(skill_root))
        assert out["names"] == ["debug"]

    def test_non_skill_entries_are_ignored(self):
        out = inject(query="anything", skills=["not a skill", None])
        assert out["count"] == 0

    def test_no_skills_at_all(self):
        assert inject(query="x", skills=None)["message"] is None


class TestSummaries:
    def test_one_line_each_sorted(self, skill_root):
        text = skill_summaries(load_skills(skill_root))
        assert text.splitlines() == ["- debug: How to debug.", "- deploy: How to deploy."]

    def test_empty_is_empty(self):
        assert skill_summaries([]) == ""
