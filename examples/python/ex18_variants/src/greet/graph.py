"""The door's graph: one module-level `@graph`, its per-variant parts as parameters.

`[serve.variants]` binds `style` and `sign_off` once per variant; `operonx serve`
compiles one engine per variant. Nothing inside the graph reads a "which variant am
I" flag: the bound values are the variant.
"""

from greet.ops import greeting
from operonx.app.serve import egress, ingress
from operonx.core import END, START, graph


@graph
def greet(style, sign_off):
    request = ingress()
    reply = greeting(who=request["item"], style=style, sign_off=sign_off)
    out = egress(item=reply["text"])
    START >> request >> reply >> out >> END
