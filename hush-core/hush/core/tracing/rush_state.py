"""RushStateAdapter — lightweight adapter for Rust's $state dict.

Makes Rust's $state metadata compatible with TraceCollector's read interface,
so FlushWorker can process Rust execution data without changes to TraceCollector.
"""

DEFAULT_CONTEXT = ""


class RushStateAdapter:
    """Wraps Rust's $state dict to provide MemoryState-compatible read interface.

    TraceCollector expects:
        state.iter_executed(op) -> yields (context_id, start_time) pairs
        state.tags             -> list of strings
        state.request_id       -> str
        state.user_id          -> str
        state.session_id       -> str
        state[op, var, ctx]    -> per-op values (I/O, timing, errors)
    """

    __slots__ = ("_data",)

    def __init__(self, state_dict: dict):
        self._data = state_dict

    def iter_executed(self, op_name: str):
        """Yield (context_id, start_time) for each execution of op_name."""
        values = self._data.get("values", {})
        start_times = values.get(op_name, {}).get("start_time", {})
        for ctx, value in start_times.items():
            if value is not None:
                yield ctx, value

    @property
    def tags(self):
        return self._data.get("tags", [])

    @property
    def request_id(self):
        return self._data.get("request_id")

    @property
    def user_id(self):
        return self._data.get("user_id")

    @property
    def session_id(self):
        return self._data.get("session_id")

    def __getitem__(self, key):
        """Support state[op_name, var_name, ctx] lookups."""
        op, var, ctx = key
        ctx_key = ctx if ctx is not None else DEFAULT_CONTEXT
        values = self._data.get("values", {})
        return values.get(op, {}).get(var, {}).get(ctx_key)
