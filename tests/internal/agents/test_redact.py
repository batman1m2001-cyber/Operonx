"""Secret redaction.

Two failure directions, and the second is the easy one to forget.
Under-redaction leaks a live credential into the model's context and the
tracer's output. **Over-redaction** produces an agent that cannot read
its own project, and the symptom — a model reasoning about
``[redacted:…]`` as though it were data — is far harder to diagnose than
a leak. Roughly half of these tests guard the second direction.
"""

from __future__ import annotations

import asyncio

import pytest

from operonx.agents.graphs.dispatch import build_dispatch
from operonx.agents.redact import Redactor
from operonx.agents.tool import clear_registry, tool
from operonx.core import Operon

pytestmark = pytest.mark.unit

R = Redactor()


class TestCatchesRealSecrets:
    @pytest.mark.parametrize(
        "text,kind",
        [
            pytest.param("sk-abcdefghijklmnop12345", "openai-key", id="openai"),
            pytest.param("sk-ant-abcdefghijklmnop123", "anthropic-key", id="anthropic"),
            pytest.param("ghp_" + "a" * 30, "github-token", id="github"),
            pytest.param("xoxb-1234567890-abcdef", "slack-token", id="slack"),
            pytest.param("AKIAIOSFODNN7EXAMPLE", "aws-access-key", id="aws"),
            pytest.param("AIza" + "b" * 35, "google-key", id="google"),
            pytest.param(
                "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijkl", "jwt", id="jwt"
            ),
        ],
    )
    def test_vendor_shapes(self, text, kind):
        assert kind in R.found(text)
        assert text not in R.scrub(f"the key is {text} ok")

    def test_pem_block(self):
        pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----"
        assert "MIIabc" not in R.scrub(pem)

    @pytest.mark.parametrize(
        "line",
        [
            'api_key = "supersecretvalue"',
            "API_KEY=supersecretvalue",
            "password: supersecretvalue",
            "access_key = supersecretvalue",
            "token: supersecretvalue",
        ],
    )
    def test_labelled_assignments(self, line):
        out = R.scrub(line)
        assert "supersecretvalue" not in out

    def test_label_survives_the_value(self):
        """The model usually needs to know a setting exists; it never
        needs to know what it is."""
        out = R.scrub('api_key = "supersecretvalue"')
        assert "api_key" in out
        assert "redacted" in out

    def test_bearer_header(self):
        out = R.scrub("Authorization: Bearer abcdefghijklmnop")
        assert "abcdefghijklmnop" not in out
        assert "Authorization" in out

    def test_url_credentials(self):
        out = R.scrub("postgres://admin:hunter2xyz@db.internal:5432/app")
        assert "hunter2xyz" not in out
        assert "db.internal" in out, "the host is not a secret and is often the point"

    def test_multiple_secrets_in_one_blob(self):
        blob = "sk-aaaaaaaaaaaaaaaaaa and AKIAIOSFODNN7EXAMPLE"
        out = R.scrub(blob)
        assert "sk-aaaaaaaaaaaaaaaaaa" not in out
        assert "AKIAIOSFODNN7EXAMPLE" not in out


class TestDoesNotOverRedact:
    @pytest.mark.parametrize(
        "text",
        [
            pytest.param("/home/user/project/src/main.py", id="path"),
            pytest.param("commit a1b2c3d4e5f6789012345678901234567890abcd", id="git-sha"),
            pytest.param("the function returns a dict of results", id="prose"),
            pytest.param('{"name": "widget", "count": 42}', id="json"),
            pytest.param("https://api.example.com/v1/users?limit=10", id="url"),
            pytest.param("error at line 42 in module foo", id="traceback"),
            pytest.param("version = 1.2.3", id="version-assignment"),
            pytest.param("name = widget", id="short-assignment"),
        ],
    )
    def test_ordinary_text_is_untouched(self, text):
        assert R.scrub(text) == text

    def test_a_long_hex_string_is_not_a_secret(self):
        """'Long string of letters' is the rule that eats a codebase."""
        sha = "deadbeef" * 8
        assert R.scrub(sha) == sha

    def test_the_word_token_alone_is_not_redacted(self):
        assert R.scrub("the token is refreshed hourly") == "the token is refreshed hourly"


class TestApi:
    def test_none_becomes_empty(self):
        assert R.scrub(None) == ""

    def test_non_strings_are_stringified(self):
        assert "42" in R.scrub({"count": 42})

    def test_found_reports_kinds(self):
        assert R.found("sk-abcdefghijklmnop12345") == ["openai-key"]

    def test_found_on_clean_text_is_empty(self):
        assert R.found("hello world") == []

    def test_extra_patterns_add_to_the_defaults(self):
        r = Redactor(extra=[("employee-id", r"\bEMP-\d{8}\b")])
        out = r.scrub("EMP-12345678 used sk-abcdefghijklmnop12345")
        assert "EMP-12345678" not in out
        assert "sk-abcdefghijklmnop12345" not in out, "defaults must still apply"

    def test_patterns_replaces_the_defaults(self):
        r = Redactor(patterns=[("only", r"\bxyzzy\b")])
        out = r.scrub("xyzzy and sk-abcdefghijklmnop12345")
        assert "xyzzy" not in out
        assert "sk-abcdefghijklmnop12345" in out

    def test_bad_pattern_raises_at_construction(self):
        with pytest.raises(ValueError, match=r"does not compile"):
            Redactor(extra=[("broken", "([unclosed")])

    def test_scrub_message_returns_a_new_dict(self):
        """Mutating in place would rewrite the caller's shared
        conversation cell, so a false positive could never be undone."""
        original = {"role": "tool", "content": "sk-abcdefghijklmnop12345"}
        out = R.scrub_message(original)
        assert "sk-" in original["content"]
        assert "sk-" not in out["content"]
        assert out["role"] == "tool"

    def test_scrub_message_ignores_a_message_without_content(self):
        message = {"role": "assistant", "tool_calls": []}
        assert R.scrub_message(message) is message


class TestDispatchIntegration:
    @pytest.fixture(autouse=True)
    def _tools(self):
        clear_registry()

        @tool(
            name="read_env",
            description="Read config.",
            schema={"type": "object", "properties": {}},
        )
        async def read_env() -> dict:
            return {"config": 'OPENAI_API_KEY="sk-abcdefghijklmnop12345"'}

        yield
        clear_registry()

    async def _run(self, redactor):
        built = build_dispatch(redactor=redactor)(call=None)
        result = await asyncio.wait_for(
            Operon(built).run(inputs={"call": {"id": "1", "name": "read_env", "args": {}}}),
            timeout=30,
        )
        return result["tool_message"]["content"]

    @pytest.mark.asyncio
    async def test_tool_output_is_scrubbed_before_the_model_sees_it(self):
        content = await self._run(Redactor())
        assert "sk-abcdefghijklmnop12345" not in content
        assert "OPENAI_API_KEY" in content, "the setting name is not the secret"

    @pytest.mark.asyncio
    async def test_disabled_by_default(self):
        """Opt-in, because over-redaction is harder to diagnose than a
        leak and most tools never touch a credential."""
        content = await self._run(None)
        assert "sk-abcdefghijklmnop12345" in content
