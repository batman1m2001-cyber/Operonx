"""The application: one door, two compiled greeters.

`operonx.toml` points here with `[project] app = "app:APP"`; this is where
a reader sees which graph runs behind which door, what the door binds per
variant, and who picks the variant.
"""

from operonx.app import Application, Service, env, http

from greet import door, styles
from greet.graph import build

APP = Application(
    "ex18-variants",
    services=[
        Service(
            "greet",
            http("POST", "/greet", port=env("HTTP_PORT", 8018)),
            graph=build,  # a factory: build(style, sign_off) -> @graph
            variants={
                "formal": dict(style=styles.formal, sign_off="Regards"),
                "casual": dict(style=styles.casual, sign_off="Cheers"),
            },
            ingress=["request"],
            egress=["out"],
            on_session=door.open,  # ?style=formal|casual -> RunRequest(variant=…)
            description="POST /greet?style=formal|casual with a JSON name; the reply is that style's greeting.",
        ),
    ],
    description="One HTTP door, two compiled greeters: the session picks the variant.",
)
