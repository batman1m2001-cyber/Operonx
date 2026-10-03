"""The chat feature's logic. Every op returns a dict literal: its keys are
the op's outputs."""

from operonx import op


@op
def read_question(body: dict) -> dict:
    """The request body's `question`, with its whitespace collapsed."""
    return {"question": " ".join(str(body.get("question", "")).split())}


@op
def reply(content: str = None, error: str = None) -> dict:
    """The answer to send back; a failed model call says so instead."""
    if content is None:
        return {"reply": {"answer": None, "error": error or "the model gave no answer"}}
    return {"reply": {"answer": content, "error": None}}
