"""The chat feature's wiring: no logic here, only ops and edges."""

from operonx import END, START, graph
from operonx.app.serve import egress, ingress
from operonx.providers.ops import LLMOp

from chat._prompts import SYSTEM
from chat.ops import read_question, reply


@graph
def chat_flow():
    src = ingress()  # an http door: the JSON body is the one item
    q = read_question(body=src["item"])
    llm = LLMOp.of(
        resource="assistant",  # `llm:assistant` in resources.yaml
        prompt={"system": SYSTEM, "user": "{question}"},
        question=q["question"],  # every other keyword fills the template
    )
    r = reply(content=llm["content"], error=llm["error"])
    out = egress(item=r["reply"])
    START >> src >> q >> llm >> r >> out >> END
