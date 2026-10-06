import asyncio

from operonx.app.jobs import Job

from chat.graph import chat_flow
from chat.ops import read_question, reply


def test_read_question_collapses_whitespace():
    assert read_question(body={"question": " what  is\noperonx? "})()["question"] == (
        "what is operonx?"
    )


def test_reply_reports_a_failed_model_call():
    out = reply(error="timeout")()
    assert out["reply"] == {"answer": None, "error": "timeout"}


def test_the_flow_answers_with_the_model(fake_model, tmp_path):
    job = Job(
        "t",
        graph=chat_flow,
        items=[{"id": "q1", "question": "Hello?"}],
        key="id",
        record_dir=tmp_path,
    )
    run = asyncio.run(job.run())
    assert run.status == "ok"
    assert run.results == {"q1": {"answer": "Echo: Hello?", "error": None}}
