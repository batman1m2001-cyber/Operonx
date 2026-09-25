"""`on_session`: the request picks its variant.

A door with variants has no default — every session names one, and an
unknown name is refused here, before a run is minted, with the declared
names in the log line.
"""

from operonx.app.serve import RunRequest


def open(session) -> RunRequest:
    style = session.meta.get("query", {}).get("style", "formal")
    return RunRequest(variant=style)
