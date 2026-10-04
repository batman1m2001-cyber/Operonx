"""Secret redaction, ported from ``operonx/tests/internal/agents/test_redact.py``.

Two failure directions, and the second is the easy one to forget.
Under-redaction leaks a live credential into the model's context and the
tracer's output. **Over-redaction** produces an agent that cannot read
its own project, and the symptom — a model reasoning about
``[redacted:…]`` as though it were data — is far harder to diagnose than
a leak. Roughly half of these tests guard the second direction.

Then where it applies in this package (track3 §4.4.8, invariant 6): on by
default for trace records and approval requests, never for what a tool
runs with, and for what the model reads only with ``RedactToolOutput``,
which runs before truncation.
"""

from __future__ import annotations

import json

import pytest
from operonx import END, START, Operon, graph, op

from operonx_agents import (
    InMemoryStateStore,
    Redactor,
    RedactToolOutput,
    Runner,
    tool,
)
from tests.agents import asks, make, says

SECRET = "sk-abcdefghijklmnop12345"

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


class TestScrubData:
    """Tool arguments arrive as a structure, and in a structure the label
    that identifies a secret is the *key* rather than text beside it."""

    def test_a_value_is_caught_by_its_key(self):
        out = R.scrub_data({"password": "correcthorse99", "api_key": "abcdef123456"})
        assert "correcthorse99" not in str(out)
        assert "abcdef123456" not in str(out)
        assert set(out) == {"password", "api_key"}, "keys survive"

    def test_nested_and_listed_values_are_reached(self):
        out = R.scrub_data({"a": [{"b": "sk-abcdefghijklmnop12345"}], "api_key": ["k_12345678"]})
        assert "sk-abcdefghijklmnop12345" not in str(out)
        assert "k_12345678" not in str(out), "list items inherit the list's key"

    def test_ordinary_values_are_untouched(self):
        """Over-redaction: `max_tokens` is not a token, a path is not a key."""
        data = {"max_tokens": 1024, "path": "src/app/settings.py", "ok": True, "n": None}
        assert R.scrub_data(data) == data

    def test_does_not_mutate_the_input(self):
        data = {"headers": {"Authorization": "Bearer sk-abcdefghijklmnop12345"}}
        R.scrub_data(data)
        assert data == {"headers": {"Authorization": "Bearer sk-abcdefghijklmnop12345"}}


@tool
async def read_env() -> str:
    """Read the config."""
    return f'OPENAI_API_KEY="{SECRET}"'


SEEN: list = []


@tool(approval="always")
async def call_api(url: str, token: str) -> str:
    """Call an API with a token."""
    SEEN.append(token)
    return "200 OK"


def traced(agent):
    @op
    async def chat(question: str) -> dict:
        res = await Runner.run(agent, question, store=InMemoryStateStore())
        return {"answer": res.output, "status": res.status}

    @graph
    def flow(question):
        c = chat(question=question)
        START >> c >> END

    return Operon(flow, params={"question": None})


class TestWhereItApplies:
    async def test_traces_are_scrubbed_by_default_and_the_model_reads_the_real_output(self, hub):
        agent, llm = make(hub, asks(("read_env", {})), says("done"), tools=[read_env])
        handle = traced(agent).start({"question": f"my key is {SECRET}"})
        assert (await handle.result())["answer"] == "done"
        mine = [n for n in handle.trace.nodes if n.op_name != "c"]  # `c` is the caller's op
        assert sorted(n.op_name for n in mine) == ["model", "model", "read_env", "turn", "turn"]
        dumped = json.dumps([(n.inputs, n.outputs) for n in mine], default=str)
        assert SECRET not in dumped, "no record of the agent's holds the key"
        assert "OPENAI_API_KEY" in dumped, "the setting's name is not the secret"
        tool_record = next(n for n in handle.trace.nodes if n.op_name == "read_env")
        assert "[redacted:" in tool_record.outputs["tool_message"]["content"]
        model_record = [n for n in handle.trace.nodes if n.op_name == "model"][1]
        assert "[redacted:" in model_record.inputs["messages"][-1]["content"]
        assert SECRET in llm.requests[1]["messages"][-1]["content"], "the model is untouched"

    async def test_redact_none_records_as_is(self, hub):
        agent, _ = make(hub, asks(("read_env", {})), says("done"), tools=[read_env], redact=None)
        handle = traced(agent).start({"question": "go"})
        await handle.result()
        tool_record = next(n for n in handle.trace.nodes if n.op_name == "read_env")
        assert SECRET in tool_record.outputs["tool_message"]["content"]

    async def test_an_approval_shows_redacted_args_and_the_tool_gets_the_real_ones(self, hub):
        SEEN.clear()
        args = {"url": "https://api.example.com", "token": "tok_live_123456789"}
        agent, _ = make(hub, asks(("call_api", args)), says("ok"), tools=[call_api])
        store = InMemoryStateStore()
        res = await Runner.run(agent, "call it", store=store)
        (asked,) = res.interruptions
        assert asked.args == {
            "url": "https://api.example.com",
            "token": "[redacted:secret-assignment]",
        }
        assert "tok_live_123456789" not in json.dumps(
            (await store.load(res.run_id)).pending.interruptions
        )
        from operonx_agents import Approve

        await Runner.resume(agent, res.run_id, store=store, approvals={asked.id: Approve()})
        assert SEEN == ["tok_live_123456789"], "tool arguments are never redacted"

    async def test_redact_tool_output_scrubs_what_the_model_reads_before_truncation(self, hub):
        @tool(max_result_chars=25)  # cuts inside the key: "padding padding sk-abcdef"
        async def dump() -> str:
            """Dump the config."""
            return f"padding padding {SECRET} and a long tail " + "x" * 100

        agent, llm = make(hub, asks(("dump", {})), says(), tools=[dump], hooks=[RedactToolOutput()])
        await Runner.run(agent, "dump")
        seen = llm.requests[1]["messages"][-1]["content"]
        assert SECRET[:10] not in seen, "a cut cannot leave half a key behind"
        assert seen.startswith("padding padding [redacted")
        assert "[truncated:" in seen
