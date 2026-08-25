"""Surgical config edits.

The contract under test: **an edit that changes nothing returns the input
byte for byte**, and an edit that changes something produces a diff a
reviewer can read. Parse-and-reserialise fails both — measured at 57–76%
of each real resource file destroyed, every comment included.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from operonx_project.edit import EditError, set_env_var, set_resource_field, unset_env_var

pytestmark = pytest.mark.unit

RESOURCES = """\
# Resource references this example uses.
llm:gpt-4o-mini:
  api_type: openai
  api_key: ${OPENAI_API_KEY}
  model: gpt-4o-mini      # cheap enough for the tutorial

# Optional — only the rerank scenario uses this.
# reranker:bge-m3:
#   api_type: vllm
#   model: BAAI/bge-reranker-v2-m3
"""

ENV = """\
# OpenAI — used by every LLM scenario.
OPENAI_API_KEY=sk-old

# LOG_LEVEL=debug
export OTHER=1
"""


class TestNoOpIsByteIdentical:
    """The round-trip gate, stated as the property it actually depends on."""

    def test_setting_the_same_env_value(self):
        assert set_env_var(ENV, "OPENAI_API_KEY", "sk-old") == ENV

    def test_unsetting_an_absent_name(self):
        assert unset_env_var(ENV, "NOT_PRESENT") == ENV

    def test_setting_the_same_resource_value(self):
        assert set_resource_field(RESOURCES, "llm:gpt-4o-mini", "api_type", "openai") == RESOURCES

    @pytest.mark.parametrize("name", ["resources.yaml", "operonx.toml", ".env.example"])
    def test_real_project_files_survive_a_no_op(self, name):
        """Against files that actually ship, not synthetic fixtures."""
        root = Path(__file__).resolve().parents[3] / "examples/python/ex16_rag_pipeline"
        path = root / name
        if not path.exists():
            pytest.skip(f"{name} not present")
        original = path.read_text(encoding="utf-8")
        if name == "resources.yaml":
            assert set_resource_field(original, "llm:gpt-4o-mini", "api_type", "openai") == original
        else:
            assert unset_env_var(original, "DEFINITELY_NOT_THERE") == original


class TestEnvEdits:
    def test_existing_value_is_replaced_in_place(self):
        out = set_env_var(ENV, "OPENAI_API_KEY", "sk-new")
        assert "OPENAI_API_KEY=sk-new" in out
        assert out.index("OPENAI_API_KEY") == ENV.index("OPENAI_API_KEY")  # same position

    def test_comments_and_other_lines_survive(self):
        out = set_env_var(ENV, "OPENAI_API_KEY", "sk-new")
        assert "# OpenAI — used by every LLM scenario." in out
        assert "# LOG_LEVEL=debug" in out
        assert "export OTHER=1" in out

    def test_export_prefix_is_preserved(self):
        assert "export OTHER=2" in set_env_var(ENV, "OTHER", "2")

    def test_a_commented_assignment_is_not_revived(self):
        """The comment usually documents a default; rewriting it destroys that."""
        out = set_env_var(ENV, "LOG_LEVEL", "info")
        assert "# LOG_LEVEL=debug" in out
        assert out.rstrip().endswith("LOG_LEVEL=info")

    def test_new_name_is_appended(self):
        out = set_env_var(ENV, "BRAND_NEW", "1")
        assert out.startswith(ENV) and out.rstrip().endswith("BRAND_NEW=1")

    def test_appending_to_a_file_without_a_trailing_newline(self):
        assert set_env_var("A=1", "B", "2") == "A=1\nB=2\n"

    def test_values_needing_quotes_are_quoted(self):
        assert 'K="a b"' in set_env_var("", "K", "a b")
        assert 'K="has#hash"' in set_env_var("", "K", "has#hash")

    def test_unset_removes_only_the_live_assignment(self):
        out = unset_env_var(ENV, "OPENAI_API_KEY")
        assert "OPENAI_API_KEY=" not in out
        assert "# OpenAI — used by every LLM scenario." in out


class TestResourceEdits:
    def test_value_is_replaced_and_the_file_otherwise_survives(self):
        out = set_resource_field(RESOURCES, "llm:gpt-4o-mini", "api_key", "${OTHER_KEY}")
        assert "api_key: ${OTHER_KEY}" in out
        assert "# Resource references this example uses." in out
        assert "# reranker:bge-m3:" in out
        assert len(out.splitlines()) == len(RESOURCES.splitlines())

    def test_trailing_comment_on_the_edited_line_survives(self):
        out = set_resource_field(RESOURCES, "llm:gpt-4o-mini", "model", "gpt-4o")
        assert "model: gpt-4o      # cheap enough for the tutorial" in out

    def test_a_commented_out_block_is_not_edited(self):
        """ex16 has a live doc_store:corpus and a dead one — editing the dead
        copy would look like success and change nothing."""
        with pytest.raises(EditError, match="not found"):
            set_resource_field(RESOURCES, "reranker:bge-m3", "model", "x")

    def test_missing_field_raises_rather_than_appending(self):
        with pytest.raises(EditError, match="no field"):
            set_resource_field(RESOURCES, "llm:gpt-4o-mini", "temperature", 0.5)

    def test_missing_resource_raises(self):
        with pytest.raises(EditError, match="not found"):
            set_resource_field(RESOURCES, "llm:absent", "api_type", "openai")

    def test_does_not_leak_into_the_next_resource(self):
        text = "a:x:\n  model: one\nb:y:\n  model: two\n"
        out = set_resource_field(text, "a:x", "model", "changed")
        assert "model: changed" in out and "model: two" in out
        with pytest.raises(EditError, match="no field"):
            set_resource_field(text, "b:y", "api_type", "z")

    @pytest.mark.parametrize(
        "value, rendered",
        [(True, "true"), (False, "false"), (None, "null"), (3, "3"), (0.7, "0.7")],
    )
    def test_scalars_render_as_yaml(self, value, rendered):
        out = set_resource_field(RESOURCES, "llm:gpt-4o-mini", "api_type", value)
        assert f"api_type: {rendered}" in out


class TestRealFileHazards:
    """Cases that only turned up when run against files that actually ship."""

    def test_trailing_comment_is_not_swallowed_into_the_value(self):
        """callbot: NEED_STT_EMB = "true"  # Whether to get embedding..."""
        text = 'NEED_STT_EMB = "true"  # Whether to get embedding from STT model\n'
        assert set_env_var(text, "NEED_STT_EMB", "true") == text
        out = set_env_var(text, "NEED_STT_EMB", "false")
        assert out == 'NEED_STT_EMB = "false"  # Whether to get embedding from STT model\n'

    def test_spaces_around_equals_are_preserved(self):
        """callbot: KEYCLOAK_REFRESH_INTERVAL = 1200"""
        text = "KEYCLOAK_REFRESH_INTERVAL = 1200\n"
        assert set_env_var(text, "KEYCLOAK_REFRESH_INTERVAL", "1200") == text
        assert set_env_var(text, "KEYCLOAK_REFRESH_INTERVAL", "600") == (
            "KEYCLOAK_REFRESH_INTERVAL = 600\n"
        )

    def test_every_duplicate_assignment_is_updated(self):
        """dotenv takes the last, so rewriting only the first changes nothing."""
        text = 'VAD_NEED_PADDING = "true"\nOTHER=1\nVAD_NEED_PADDING = "false"\n'
        out = set_env_var(text, "VAD_NEED_PADDING", "true")
        assert out.count('VAD_NEED_PADDING = "true"') == 2
        assert '"false"' not in out

    def test_a_hash_inside_a_quoted_value_is_not_a_comment(self):
        text = 'K = "a#b"\n'
        assert set_env_var(text, "K", "a#b") == text

    def test_quoted_yaml_value_keeps_its_quotes(self):
        """operon: api_version: "2025-04" — bare, YAML may not read it back as str."""
        text = 'reranking:bge-m3:\n  api_version: "2025-04"\n'
        assert set_resource_field(text, "reranking:bge-m3", "api_version", "2025-04") == text

    def test_a_value_that_would_change_type_when_bare_is_quoted(self):
        text = "llm:x:\n  flag: placeholder\n"
        out = set_resource_field(text, "llm:x", "flag", "true")
        assert 'flag: "true"' in out, "bare `true` would read back as a bool"
