"""The element ops of the greeter."""

from operonx.core.ops import op


@op(bound="sync")
def greeting(who: str = "", style=None, sign_off: str = "") -> dict:
    """One line: the style's opening, the name, the sign-off."""
    return {"text": f"{style(who)} {sign_off}."}
