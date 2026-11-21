"""PROMPTS_PLAN P1: an agent's instructions asked for by name."""

from __future__ import annotations

from operonx.prompts import prompt

from operonx_agents import Agent, AgentOp, Model


def test_instructions_by_name_are_sent_as_written_and_the_row_names_them(tmp_path, monkeypatch):
    (tmp_path / "operonx.toml").write_text('name = "p"\n')
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "declare.agent.md").write_text(
        'Reply as {"note": "..."}; never guess.\n'
    )
    monkeypatch.chdir(tmp_path)

    agent = Agent(name="declare", model=Model("m"), instructions=prompt("declare.agent"))
    # the file's text, not the brace-doubled template an LLMOp formats
    assert agent.system_prompt(None) == 'Reply as {"note": "..."}; never guess.'

    step = AgentOp(agent=agent, name="declare")
    (ref,) = step._record_attrs["prompts"]
    assert (ref["part"], ref["name"], ref["version"]) == ("instructions", "declare.agent", None)


def test_inline_instructions_record_nothing():
    step = AgentOp(agent=Agent(name="a", model=Model("m"), instructions="Be brief."), name="a")
    assert getattr(step, "_record_attrs", None) is None
