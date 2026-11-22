"""PROMPTS_PLAN P1/P2: a prompt is a named file, an op asks for it by name,
a label picks its version, and the run row says which was used."""

import copy
import pickle

import pytest

from operonx import END, START, Operon, graph
from operonx.prompts import (
    PromptFormatError,
    PromptNotFound,
    PromptText,
    prompt,
    prompt_names,
    prompt_refs,
)
from operonx.providers.ops import LLMOp
from operonx.testing import fake_model

pytestmark = pytest.mark.unit


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / "operonx.toml").write_text('name = "p"\n')
    d = tmp_path / "prompts"
    d.mkdir()
    (d / "guard.md").write_text('Answer with JSON only: {"ok": true}\n')
    (d / "intake.system.md").write_text(
        "---\ndescription: the intake prompt\n---\nYou help {{ customer }}.\n"
        'Answer with JSON only: {"ok": true}\n'
    )
    (d / "ask.md").write_text("Q: {{question}}\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPERONX_PROMPT_LABEL", raising=False)
    # another test's Application may have left its project set
    from operonx.core import workflow_trace

    monkeypatch.setattr(workflow_trace, "_PROJECT_ROOT", [None])
    return tmp_path


def test_a_prompt_is_its_file_and_it_knows_its_name(project):
    p = prompt("intake.system")
    assert p.text == 'You help {{ customer }}.\nAnswer with JSON only: {"ok": true}'
    assert (p.name, p.version, p.label) == ("intake.system", None, None)
    assert p.variables == ("customer",)
    assert p.meta == {"description": "the intake prompt"}
    assert prompt_names() == ["ask", "guard", "intake.system"]


def test_only_double_braces_are_variables_and_str_is_the_text_as_written(project):
    p = prompt("intake.system")
    assert not isinstance(p, str) and str(p) == p.text
    assert p.render(customer="Lan") == 'You help Lan.\nAnswer with JSON only: {"ok": true}'
    assert p.fill({"customer": "Lan"}) == p.render(customer="Lan")
    with pytest.raises(KeyError, match="customer"):
        p.render()


def test_a_missing_name_says_what_the_project_has(project):
    with pytest.raises(PromptNotFound, match="ask, guard, intake.system"):
        prompt("intake.sistem")
    with pytest.raises(PromptNotFound, match="not a prompt name"):
        prompt("../secrets")


def test_an_include_is_refused_and_names_the_composite_way(project):
    (project / "prompts" / "a.md").write_text("A\n{{> b}}")
    with pytest.raises(PromptFormatError, match=r"\{\{> b\}\}.*composite"):
        prompt("a")


def test_a_label_picks_the_version_and_moving_it_is_the_rollback(project, monkeypatch):
    d = project / "prompts"
    (d / ".versions" / "ask").mkdir(parents=True)
    (d / ".versions" / "ask" / "1.md").write_text("v1 {{question}}\n")
    (d / ".versions" / "ask" / "2.md").write_text("v2 {{question}}\n")
    (d / "labels.toml").write_text(
        "[ask]\nproduction = 2\nstaging = 1\n\n[intake.system]\nstaging = 7\n"
    )

    p = prompt("ask")
    assert (p.text, p.version, p.label) == ("v2 {{question}}", 2, "production")
    monkeypatch.setenv("OPERONX_PROMPT_LABEL", "staging")
    assert prompt("ask").version == 1
    assert prompt("ask", label="production").version == 2
    assert prompt("ask", version=1).text == "v1 {{question}}"
    # no label of that name: the working text
    assert prompt("guard").version is None
    assert ".versions" not in " ".join(prompt_names())

    (d / "labels.toml").write_text("[ask]\nproduction = 1\n")
    monkeypatch.delenv("OPERONX_PROMPT_LABEL")
    assert prompt("ask").text == "v1 {{question}}"
    with pytest.raises(PromptNotFound, match="no version 9"):
        prompt("ask", version=9)


def test_a_copy_and_a_pickle_keep_what_it_is(project):
    p = prompt("ask")
    for q in (
        copy.deepcopy(p),
        pickle.loads(pickle.dumps(p)),
        copy.deepcopy({"system": p})["system"],
    ):
        assert isinstance(q, PromptText) and q.ref == p.ref and q == p


def test_refs_of_a_template(project):
    g, a = prompt("guard"), prompt("ask")
    assert prompt_refs(a) == [a.ref]
    assert prompt_refs({"system": g, "user": "{q}"}) == [{"part": "system", **g.ref}]
    assert prompt_refs("plain {q}") == [] and prompt_refs(None) == []


async def test_an_llm_op_formats_it_and_the_run_row_names_it(project):
    @graph
    def flow(customer, question):
        s = LLMOp.of(
            resource="assistant",
            prompt={"system": prompt("intake.system"), "user": prompt("ask")},
            customer=customer,
            question=question,
        )
        START >> s >> END

    with fake_model("fine") as model:
        handle = Operon(flow, params={"customer": None, "question": None}).start(
            {"customer": "Lan", "question": "why?"}
        )
        out = await handle.result()
    assert out.get("content") == "fine"
    sent = model.calls[0]
    sent = sent.get("messages", sent) if isinstance(sent, dict) else sent
    assert [m["content"] for m in sent] == [
        'You help Lan.\nAnswer with JSON only: {"ok": true}',
        "Q: why?",
    ]
    row = next(n for n in handle.trace.nodes if n.op_name == "s")
    assert [(r["part"], r["name"], r["version"]) for r in row.attrs["prompts"]] == [
        ("system", "intake.system", None),
        ("user", "ask", None),
    ]
    assert all(len(r["hash"]) == 10 for r in row.attrs["prompts"])


async def test_an_op_with_an_inline_prompt_records_nothing_new(project):
    @graph
    def flow(q):
        s = LLMOp.of(resource="assistant", prompt="{q}", q=q)
        START >> s >> END

    with fake_model("ok"):
        handle = Operon(flow, params={"q": None}).start({"q": "x"})
        await handle.result()
    assert "prompts" not in next(n for n in handle.trace.nodes if n.op_name == "s").attrs


async def test_a_prompt_rendered_upstream_is_named_on_the_llm_ops_row(project):
    """The prompt is chosen at run time (an op renders it); the LLM op that
    is handed the text still says which prompt."""
    import copy
    import pickle

    from operonx.core import op

    @op
    def compose(customer: str):
        return {"system": prompt("intake.system").render(customer=customer)}

    @graph
    def flow(customer):
        c = compose(customer=customer)
        s = LLMOp.of(
            resource="assistant", prompt={"system": "{sys}", "user": "hello"}, sys=c["system"]
        )
        START >> c >> s >> END

    with fake_model("fine") as model:
        handle = Operon(flow, params={"customer": None}).start({"customer": "Lan"})
        await handle.result()
    sent = model.calls[0]
    sent = sent.get("messages", sent) if isinstance(sent, dict) else sent
    assert sent[0]["content"] == 'You help Lan.\nAnswer with JSON only: {"ok": true}'
    row = next(n for n in handle.trace.nodes if n.op_name == "s")
    assert [(r["input"], r["name"]) for r in row.attrs["prompts"]] == [("sys", "intake.system")]
    # a prompt is not a variable's value: link it in a composite
    with pytest.raises(TypeError, match="composite"):
        prompt("intake.system").render(customer=prompt("ask").render(question="why?"))

    r = prompt("ask").render(question="x")
    assert r == "Q: x" and r.refs == (prompt("ask").ref,)
    assert pickle.loads(pickle.dumps(r)).refs == r.refs and copy.deepcopy(r).refs == r.refs


def test_publish_label_diff_from_the_command_line(project, capsys):
    from operonx.cli.prompt import main
    from operonx.prompts import publish, set_label, versions

    assert main(["publish", "ask", "--label", "production"]) == 0
    assert publish("ask") == (1, False)  # the same text is not published twice
    (project / "prompts" / "ask.md").write_text("Question: {{question}}\n")
    assert main(["publish", "ask"]) == 0
    assert versions("ask") == [1, 2]
    assert (
        prompt("ask").version == 1 and prompt("ask").text == "Q: {{question}}"
    )  # production still 1

    assert main(["label", "ask", "production", "2"]) == 0
    assert prompt("ask").text == "Question: {{question}}"
    assert set_label("ask", "production", 1) == 2  # the rollback
    assert prompt("ask").version == 1
    assert main(["label", "ask", "production", "9"]) == 1

    capsys.readouterr()
    assert main(["diff", "ask", "1", "2"]) == 0
    got = capsys.readouterr().out
    assert "-Q: {{question}}" in got and "+Question: {{question}}" in got
    assert main(["list"]) == 0 and "production = 1" in capsys.readouterr().out
    assert main(["log", "ask"]) == 0 and "← production" in capsys.readouterr().out
    assert main(["show", "nope"]) == 1
