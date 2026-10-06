"""The application: one door, two compiled greeters.

`operonx.toml` points here with `[project] app = "app:APP"`; this is where
a reader sees which graph runs behind which door, what the door binds per
variant, and who picks the variant.
"""

from greet import door, styles
from greet.graph import greet

from operonx.app import Application, Service, env, http

APP = Application(
    "ex18-variants",
    services=[
        Service(
            "greet",
            http("POST", "/greet", port=env("HTTP_PORT", 8018)),
            graph=greet,  # @graph greet(style, sign_off): each variant binds both
            variants={
                "formal": dict(style=styles.formal, sign_off="Regards"),
                "casual": dict(style=styles.casual, sign_off="Cheers"),
            },
            on_session=door.open,  # ?style=formal|casual -> RunRequest(variant=…)
            description="POST /greet?style=formal|casual with a JSON name; the reply is that style's greeting.",
        ),
    ],
    description="One HTTP door, two compiled greeters: the session picks the variant.",
)
