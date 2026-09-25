"""The door's graph — as a factory over what differs per variant.

`build` is what `[[serve]] graph` names. It is a plain function, not a
`@graph`: the manifest's `[serve.variants]` calls it once per variant
with the bound parameters, and the `@graph` it returns is what gets
compiled. Nothing inside the graph reads a "which variant am I" flag;
the variant *is* the graph.
"""

from greet.ops import greeting
from operonx.app.serve import egress, ingress
from operonx.core import END, START, graph


def build(style, sign_off: str):
    @graph
    def greet():
        request = ingress()
        reply = greeting(who=request["item"], style=style, sign_off=sign_off)
        out = egress(item=reply["text"])
        START >> request >> reply >> out >> END

    return greet
