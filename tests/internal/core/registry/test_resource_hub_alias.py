"""``ResourceHub.alias`` — name a role at the call site, pick the
resource that fills it elsewhere.

Tooling that reads a graph without importing it can only see a literal
``resource="scanner"``. An alias is what lets that literal stay literal
while an operator still swaps the model through the environment.

The alternative — ``register()`` — writes through to storage and rewrites
``resources.yaml`` without its comments, so it is the wrong tool for a
per-process indirection.
"""

import pytest
import yaml

# Registers the `llm` category so configs parse into LLMConfig rather
# than staying raw dicts — the same import any real app does.
import operonx.providers  # noqa: F401
from operonx.core.registry import ResourceHub


@pytest.fixture
def hub(tmp_path):
    path = tmp_path / "resources.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "llm:fast": {
                    "api_type": "openai",
                    "api_key": "k1",
                    "base_url": "http://a",
                    "model": "fast-model",
                },
                "llm:slow": {
                    "api_type": "openai",
                    "api_key": "k2",
                    "base_url": "http://b",
                    "model": "slow-model",
                },
            }
        ),
        encoding="utf-8",
    )
    return ResourceHub.from_yaml(path)


class TestAliasResolution:
    def test_config_resolves_through_the_alias(self, hub):
        hub.alias("llm:scanner", "llm:fast")
        assert hub.get_config("llm:scanner").model == "fast-model"

    def test_has_sees_an_alias(self, hub):
        hub.alias("llm:scanner", "llm:fast")
        assert hub.has("llm:scanner")

    def test_unknown_key_is_still_unknown(self, hub):
        assert not hub.has("llm:nope")

    def test_alias_to_a_missing_target_does_not_resolve(self, hub):
        """Declaring is cheap and unchecked; the miss surfaces on use,
        with the normal not-found message naming the real key."""
        hub.alias("llm:scanner", "llm:ghost")
        assert not hub.has("llm:scanner")

    def test_real_keys_keep_working(self, hub):
        hub.alias("llm:scanner", "llm:fast")
        assert hub.get_config("llm:slow").model == "slow-model"


class TestRepointing:
    def test_re_aliasing_moves_existing_call_sites(self, hub):
        hub.alias("llm:scanner", "llm:fast")
        assert hub.get_config("llm:scanner").model == "fast-model"
        hub.alias("llm:scanner", "llm:slow")
        assert hub.get_config("llm:scanner").model == "slow-model"

    def test_unalias(self, hub):
        hub.alias("llm:scanner", "llm:fast")
        assert hub.unalias("llm:scanner") is True
        assert hub.has("llm:scanner") is False
        assert hub.unalias("llm:scanner") is False

    def test_aliases_returns_a_copy(self, hub):
        hub.alias("llm:scanner", "llm:fast")
        snapshot = hub.aliases()
        snapshot["llm:other"] = "llm:slow"
        assert "llm:other" not in hub.aliases()


class TestGuards:
    def test_self_reference_refused(self, hub):
        with pytest.raises(ValueError, match="cannot point at itself"):
            hub.alias("llm:scanner", "llm:scanner")

    def test_chain_refused_at_declaration(self, hub):
        """One hop only. A chain is refused where the mistake is, not as a
        hang at first use."""
        hub.alias("llm:a", "llm:fast")
        with pytest.raises(ValueError, match="itself an alias"):
            hub.alias("llm:b", "llm:a")


class TestDoesNotTouchStorage:
    def test_yaml_is_left_alone(self, tmp_path, hub):
        """The whole reason this is not ``register()``."""
        path = hub.source_path
        before = path.read_text(encoding="utf-8")
        hub.alias("llm:scanner", "llm:fast")
        hub.get_config("llm:scanner")
        assert path.read_text(encoding="utf-8") == before

    def test_a_second_hub_does_not_inherit_aliases(self, hub):
        hub.alias("llm:scanner", "llm:fast")
        other = ResourceHub.from_yaml(hub.source_path)
        assert other.aliases() == {}
