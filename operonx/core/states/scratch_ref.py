"""Marker returned by `SCRATCH[key]` at graph-construction time.

Falls through `_params.resolve_value()` and `StateSchema._build()` as a
literal default; resolved to the live scratch value by the post-resolve
pass at the end of `BaseOp.get_inputs()`.
"""


class ScratchRef:
    __slots__ = ("key",)

    def __init__(self, key: str) -> None:
        self.key = key

    def __repr__(self) -> str:
        return f"SCRATCH[{self.key!r}]"
