"""Tests for @op(exclude/include/observe_max) filter kwargs.

Covers Phase 2 filter API:
    - exclude=[list] or dict form; validated at decoration time
    - include=[list] or dict form
    - Mutual exclusion between exclude and include
    - observe_max: int | None; stored on the op instance
    - Per-call kwargs override @op-decoration defaults
"""

import pytest

from operonx.core.ops.base import _normalise_observability
from operonx.core.ops.transform.func_op import op

# ---------------------------------------------------------------------------
# _normalise_observability — the shared validator
# ---------------------------------------------------------------------------


class TestNormaliseObservability:
    def test_both_none_returns_empty_dicts(self):
        exc, inc = _normalise_observability(None, None)
        assert exc == {} and inc == {}

    def test_list_exclude_becomes_wildcard(self):
        exc, _ = _normalise_observability(["tokens", "audio"], None)
        assert exc == {"*": ("tokens", "audio")}

    def test_list_include_becomes_wildcard(self):
        _, inc = _normalise_observability(None, ["summary"])
        assert inc == {"*": ("summary",)}

    def test_dict_exclude_per_channel(self):
        exc, _ = _normalise_observability({"trace": ["pii"], "checkpoint": ["blob"]}, None)
        assert exc == {"trace": ("pii",), "checkpoint": ("blob",)}

    def test_dict_include_per_channel(self):
        _, inc = _normalise_observability(None, {"trace": ["summary"]})
        assert inc == {"trace": ("summary",)}

    def test_empty_list_include_silences_op(self):
        _, inc = _normalise_observability(None, [])
        assert inc == {"*": ()}

    def test_mutual_exclusion_raises(self):
        with pytest.raises(ValueError, match="cannot mix exclude= and include="):
            _normalise_observability(["a"], ["b"])

    def test_unknown_channel_raises(self):
        with pytest.raises(ValueError, match="unknown channels"):
            _normalise_observability({"typo": ["x"]}, None)

    def test_non_str_var_raises(self):
        with pytest.raises(TypeError, match="list entries must be str"):
            _normalise_observability([1, 2], None)

    def test_non_list_channel_value_raises(self):
        with pytest.raises(TypeError, match="value must be list"):
            _normalise_observability({"trace": "not-a-list"}, None)

    def test_scalar_arg_raises(self):
        with pytest.raises(TypeError, match="must be list"):
            _normalise_observability("tokens", None)


# ---------------------------------------------------------------------------
# @op decorator plumbing
# ---------------------------------------------------------------------------


class TestOpDecoratorKwargs:
    def test_bare_op_has_empty_filters(self):
        @op
        def bare():
            return {}

        instance = bare()
        assert instance._obs_exclude == {}
        assert instance._obs_include == {}
        assert instance.observe_max is None

    def test_op_with_exclude_list(self):
        @op(exclude=["tokens", "audio"])
        def transcribe():
            return {}

        i = transcribe()
        assert i._obs_exclude == {"*": ("tokens", "audio")}
        assert i._obs_include == {}

    def test_op_with_include_list(self):
        @op(include=["summary"])
        def summarize():
            return {}

        i = summarize()
        assert i._obs_include == {"*": ("summary",)}
        assert i._obs_exclude == {}

    def test_op_with_include_empty_silences(self):
        @op(include=[])
        def internal():
            return {}

        instance = internal()
        assert instance._obs_include == {"*": ()}

    def test_op_with_dict_exclude(self):
        @op(exclude={"trace": ["pii"], "checkpoint": ["blob"]})
        def sensitive():
            return {}

        instance = sensitive()
        assert instance._obs_exclude == {
            "trace": ("pii",),
            "checkpoint": ("blob",),
        }

    def test_op_with_observe_max(self):
        @op(observe_max=10_000)
        def bursty():
            return {}

        instance = bursty()
        assert instance.observe_max == 10_000

    def test_op_mutual_exclusion_at_decoration(self):
        with pytest.raises(ValueError, match="cannot mix exclude= and include="):

            @op(exclude=["a"], include=["b"])
            def bad():
                return {}


class TestPerCallOverride:
    def test_per_call_exclude_overrides_decoration(self):
        @op(exclude=["decorated"])
        def flex():
            return {}

        i = flex(exclude=["per_call"])
        assert i._obs_exclude == {"*": ("per_call",)}

    def test_per_call_include_overrides_decoration(self):
        @op(include=["decorated"])
        def flex():
            return {}

        i = flex(include=["per_call"])
        assert i._obs_include == {"*": ("per_call",)}

    def test_per_call_observe_max_overrides(self):
        @op(observe_max=1_000)
        def flex():
            return {}

        i = flex(observe_max=50)
        assert i.observe_max == 50

    def test_no_per_call_kwarg_keeps_decoration(self):
        @op(exclude=["decorated"], observe_max=500)
        def flex():
            return {}

        i = flex()
        assert i._obs_exclude == {"*": ("decorated",)}
        assert i.observe_max == 500
