"""PROMPT_OBJECTS_PLAN O1: a prompt is an object — a text (``.md``) or a
composite of links (``.yaml``: use / pick / when / var / text) — with an id;
data is ``{{x}}``, a prompt is never a variable's value."""

import copy
import pickle
import warnings

import pytest

from operonx import END, START, Operon, graph
from operonx.prompts import (
    CompositePrompt,
    PromptFormatError,
    PromptText,
    ensure_id,
    move,
    prompt,
    publish,
    versions,
)
from operonx.providers.ops import LLMOp
from operonx.testing import fake_model

pytestmark = pytest.mark.unit

CHAT = """\
id: pr_0000c0de
kind: chat
system:
  - use: persona
  - pick: tier
    cases:
      gold: tier/gold
      silver: tier/silver
    else: none
  - when: urgent
    use: urgent
  - var: facts
user:
  - ask
"""


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / "operonx.toml").write_text('name = "p"\n')
    d = tmp_path / "prompts"
    (d / "tier").mkdir(parents=True)
    (d / "persona.md").write_text("---\nid: pr_00000001\n---\nYou help {{customer}}.\n")
    (d / "tier" / "gold.md").write_text("Gold: be quick.\n")
    (d / "tier" / "silver.md").write_text("Silver.\n")
    (d / "urgent.md").write_text("It is urgent.\n")
    (d / "ask.md").write_text("Q: {{question}}\n")
    (d / "chat.yaml").write_text(CHAT)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPERONX_PROMPT_LABEL", raising=False)
    from operonx.core import workflow_trace

    monkeypatch.setattr(workflow_trace, "_PROJECT_ROOT", [None])
    return tmp_path


DATA = {"customer": "Lan", "tier": "gold", "urgent": True, "facts": "F1", "question": "why?"}


def test_a_chat_composite_renders_each_role_from_its_links(project):
    c = prompt("chat")
    assert isinstance(c, CompositePrompt) and c.kind == "chat" and c.id == "pr_0000c0de"
    out = c.render(**DATA)
    assert out.system == "You help Lan.\n\nGold: be quick.\n\nIt is urgent.\n\nF1"
    assert out.user == "Q: why?"
    assert out.messages == [
        {"role": "system", "content": out.system},
        {"role": "user", "content": out.user},
    ]
    assert [r["name"] for r in out.refs] == ["chat", "persona", "tier/gold", "urgent", "ask"]
    assert set(c.variables) == {"customer", "tier", "urgent", "facts", "question"}


def test_empty_parts_drop_out_and_the_tree_says_what_was_chosen(project):
    out = prompt("chat").render(**{**DATA, "tier": "bronze", "urgent": False, "facts": ""})
    assert out.system == "You help Lan."  # else: none, when off, an empty var — no stray joins
    nodes = next(r for r in out.tree["parts"] if r["role"] == "system")["parts"]
    pick = next(n for n in nodes if "pick" in n)
    when = next(n for n in nodes if "when" in n)
    assert (pick["value"], pick["use"]) == ("bronze", None)
    assert (when["value"], when["use"]) == (False, None)


def test_a_role_is_a_text_composite_of_its_own(project):
    sys = prompt("chat")["system"]
    assert isinstance(sys, CompositePrompt) and sys.kind == "text"
    assert sys.render(**DATA) == "You help Lan.\n\nGold: be quick.\n\nIt is urgent.\n\nF1"


def test_a_pick_with_no_else_refuses_a_value_it_has_no_case_for(project):
    (project / "prompts" / "strict.yaml").write_text(
        "parts:\n  - pick: tier\n    cases: {gold: tier/gold}\n"
    )
    assert prompt("strict").render(tier="gold") == "Gold: be quick."
    with pytest.raises(KeyError, match="no case"):
        prompt("strict").render(tier="silver")
    with pytest.raises(KeyError, match="no value for tier"):
        prompt("strict").render()


def test_text_parts_and_join_are_kept_as_written(project):
    (project / "prompts" / "glued.yaml").write_text(
        'join: ""\nparts:\n  - text: "<"\n  - var: x\n  - text: ">"\n'
    )
    assert prompt("glued").render(x="") == "<>"
    assert prompt("glued").render(x="a") == "<a>"
    assert prompt("glued").render(x=True) == "<True>" and prompt("glued").render(x=None) == "<>"


@pytest.mark.parametrize(
    "body, match",
    [
        ("parts:\n  - use: nope\n", "nope"),
        ("parts:\n  - use: ask\n    pick: x\n", "pick takes"),
        ("parts:\n  - var: x\n    text: y\n", "one of use"),
        ("nonsense: 1\nparts: [ask]\n", "unknown key"),
        ("kind: chat\nparts: [ask]\n", "has system / user"),
        ("kind: poem\nparts: [ask]\n", "kind is chat or text"),
        ("parts:\n  - pick: x\n", "pick needs cases"),
        ("parts:\n  - var: 'not a name'\n", "not a variable name"),
        ("{}\n", "no parts"),
    ],
)
def test_a_malformed_composite_fails_on_load_and_says_where(project, body, match):
    (project / "prompts" / "bad.yaml").write_text(body)
    with pytest.raises((PromptFormatError, LookupError), match=match):
        prompt("bad")


def test_composites_that_link_each_other_are_refused(project):
    (project / "prompts" / "a.yaml").write_text("parts: [b]\n")
    (project / "prompts" / "b.yaml").write_text("parts: [a]\n")
    with pytest.raises(PromptFormatError, match="a → b → a"):
        prompt("a")


def test_a_name_with_both_a_text_and_a_composite_is_refused(project):
    (project / "prompts" / "ask.yaml").write_text("parts: [persona]\n")
    with pytest.raises(PromptFormatError, match="ask"):
        prompt("ask")


def test_a_prompt_is_never_a_variables_value(project):
    with pytest.raises(TypeError, match="a variable is data"):
        prompt("chat").render(**{**DATA, "facts": prompt("urgent")})
    with pytest.raises(TypeError):
        prompt("ask").fill({"question": prompt("urgent")})


def test_the_old_ways_still_work_with_a_deprecation_warning(project):
    (project / "prompts" / "old.md").write_text("A\n{{> urgent}}\n")
    with pytest.warns(DeprecationWarning, match="composite"):
        assert prompt("old").text == "A\nIt is urgent."
    with pytest.warns(DeprecationWarning):
        prompt("ask").render(question=prompt("urgent"))


def test_a_copy_and_a_pickle_keep_a_composite(project):
    c = prompt("chat")
    assert copy.deepcopy(c) is c and copy.copy(c) is c
    out = c.render(**DATA)
    for r in (pickle.loads(pickle.dumps(out.system)), copy.deepcopy(out.system)):
        assert r == out.system and r.refs == out.system.refs


async def test_an_llm_op_given_a_composite_sends_its_roles_and_its_row_has_the_tree(project):
    @graph
    def flow(customer, tier, urgent, facts, question):
        s = LLMOp.of(
            resource="assistant",
            prompt=prompt("chat"),
            customer=customer,
            tier=tier,
            urgent=urgent,
            facts=facts,
            question=question,
        )
        START >> s >> END

    with fake_model("fine") as model:
        handle = Operon(flow, params={k: None for k in DATA}).start(DATA)
        out = await handle.result()
    assert out.get("content") == "fine"
    sent = model.calls[0]
    sent = sent.get("messages", sent) if isinstance(sent, dict) else sent
    assert [m["content"] for m in sent] == [
        "You help Lan.\n\nGold: be quick.\n\nIt is urgent.\n\nF1",
        "Q: why?",
    ]
    row = next(n for n in handle.trace.nodes if n.op_name == "s")
    assert [r["name"] for r in row.attrs["prompts"]][:2] == ["chat", "persona"]
    assert "tier/gold" in [r["name"] for r in row.attrs["prompts"]]
    assert row.attrs["prompt_tree"]["name"] == "chat"


async def test_cache_system_sends_the_system_message_as_a_cached_prefix(project):
    (project / "prompts" / "cached.yaml").write_text(
        CHAT.replace("kind: chat", "kind: chat\ncache: system")
    )
    out = prompt("cached").render(**DATA)
    assert out.system == prompt("chat").render(**DATA).system  # the same text
    assert out.messages == [
        {
            "role": "system",
            "content": [
                {"type": "text", "text": out.system, "cache_control": {"type": "ephemeral"}}
            ],
        },
        {"role": "user", "content": "Q: why?"},
    ]

    @graph
    def flow(customer, tier, urgent, facts, question):
        s = LLMOp.of(
            resource="assistant",
            prompt=prompt("cached"),
            customer=customer,
            tier=tier,
            urgent=urgent,
            facts=facts,
            question=question,
        )
        START >> s >> END

    with fake_model("fine") as model:
        await Operon(flow, params={k: None for k in DATA}).start(DATA).result()
    sent = model.calls[0]
    sent = sent.get("messages", sent) if isinstance(sent, dict) else sent
    assert sent[0]["content"][0]["cache_control"] == {"type": "ephemeral"}


@pytest.mark.parametrize(
    "body",
    ["cache: system\nparts: [ask]\n", "kind: chat\ncache: user\nuser: [ask]\n"],
)
def test_cache_is_only_a_chat_objects_system(project, body):
    (project / "prompts" / "bad.yaml").write_text(body)
    with pytest.raises(PromptFormatError, match="cache: system"):
        prompt("bad")


def test_ensure_id_mints_once_into_the_working_file(project):
    assert ensure_id("persona") == ("pr_00000001", False)
    new, written = ensure_id("ask")
    assert written and new.startswith("pr_") and len(new) == 11
    assert ensure_id("ask") == (new, False)
    assert prompt("ask").id == new and prompt("ask").text == "Q: {{question}}"
    (project / "prompts" / "plain.yaml").write_text("parts: [ask]\n")
    yid, _ = ensure_id("plain")
    assert prompt("plain").id == yid
    publish("ask")
    (project / "prompts" / "ask.md").write_text("Question: {{question}}\n")  # rewritten whole
    assert ensure_id("ask") == (new, True)  # the last version's id comes back
    (project / "prompts" / "odd.md").write_text("---\nid: 42\n---\nx\n")
    with pytest.raises(PromptFormatError, match="pr_"):
        ensure_id("odd")


def test_publishing_a_composite_versions_it_with_an_id(project):
    (project / "prompts" / "plain.yaml").write_text("parts: [ask]\n")
    assert publish("plain") == (1, True)
    assert versions("plain") == [1]
    assert prompt("plain", version=1).id is not None
    assert prompt("plain", version=1).render(question="q") == "Q: q"


def test_mv_renames_a_prompt_and_every_link_to_it_and_keeps_its_id(project):
    publish("chat")
    (project / "prompts" / "inc.md").write_text("X {{> tier/gold}}\n")
    changed = move("tier/gold", "tiers/gold")
    names = {p.relative_to(project / "prompts").as_posix() for p in changed}
    assert {"tiers/gold.md", "chat.yaml", "inc.md"} <= names
    assert not (project / "prompts" / "tier" / "gold.md").exists()
    assert "gold: tiers/gold" in (project / "prompts" / "chat.yaml").read_text()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        assert prompt("inc").text == "X Gold: be quick."
    assert prompt("chat").render(**DATA).system.startswith("You help Lan.\n\nGold")
    assert prompt("chat", version=1).render(**DATA).system.startswith("You help Lan.\n\nGold")
    before = prompt("persona").id
    move("persona", "who")
    assert prompt("who").id == before
    with pytest.raises(PromptFormatError, match="already exists"):
        move("who", "ask")


def test_the_command_line_shows_a_composites_tree_and_moves_a_prompt(project, capsys):
    from operonx.cli.prompt import main

    assert main(["show", "chat"]) == 0
    got = capsys.readouterr().out
    assert "pick tier:" in got and "gold → tier/gold" in got and "else → none" in got
    assert "when urgent:" in got and "var facts" in got and "user:" in got
    assert main(["list"]) == 0
    got = capsys.readouterr().out
    assert "chat" in got and "pr_0000c0de" in got and "(no id)" in got
    assert main(["mv", "urgent", "hurry"]) == 0
    assert "use: hurry" in (project / "prompts" / "chat.yaml").read_text()
    assert main(["show", "nope"]) == 1


def test_lint_c19_errors_on_a_broken_object_and_warns_on_the_old_ways(project):
    from operonx.lint.rules import lint_path

    p = project / "prompts"
    (p / "broken.yaml").write_text("parts: [missing]\n")
    (p / "old.md").write_text("{{> ask}}\nHi <<name>> and {name}\n")
    (project / "src").mkdir()
    (project / "src" / "flow.py").write_text(
        "from operonx.prompts import prompt\n"
        "from operonx.providers.ops import LLMOp\n"
        "x = prompt('ask').render(question=prompt('urgent'))\n"
        "s = LLMOp(prompt='''line one\nline two''')\n"
        "t = LLMOp(prompt='{q}')\n"
        "u = prompt('ask').text.replace('<<q>>', 'x')\n"
    )
    got = [f for f in lint_path(project) if f.rule == "C19"]
    errors = [f for f in got if f.severity == "error"]
    warns = sorted((f.file.name, f.line, f.message[:12]) for f in got if f.severity == "warning")
    assert [f.file.name for f in errors] == ["broken.yaml"]
    assert [w[:2] for w in warns] == [
        ("flow.py", 3),
        ("flow.py", 4),
        ("flow.py", 7),
        ("old.md", 1),
        ("old.md", 2),
        ("old.md", 2),
    ]
