"""Tests for the standard reducer library."""

import pytest

from operonx.reducers import (
    REMOVE_ALL_MESSAGES,
    RemoveMessage,
    add_messages,
    dict_merge,
)


class _Msg:
    """Message-like object with .id attribute."""

    __slots__ = ("id", "content")

    def __init__(self, id, content):
        self.id = id
        self.content = content

    def __eq__(self, other):
        return isinstance(other, _Msg) and self.id == other.id and self.content == other.content

    def __repr__(self):
        return f"_Msg(id={self.id!r}, content={self.content!r})"


class TestAddMessages:
    def test_append_when_ids_are_disjoint(self):
        old = [{"id": "a", "text": "hi"}]
        new = [{"id": "b", "text": "world"}]
        assert add_messages(old, new) == [
            {"id": "a", "text": "hi"},
            {"id": "b", "text": "world"},
        ]

    def test_upsert_replaces_by_id(self):
        old = [{"id": "a", "text": "old"}, {"id": "b", "text": "keep"}]
        new = [{"id": "a", "text": "new"}]
        assert add_messages(old, new) == [
            {"id": "a", "text": "new"},
            {"id": "b", "text": "keep"},
        ]

    def test_upsert_preserves_position(self):
        # Upserting id "a" should keep its original position, not move it.
        old = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
        new = [{"id": "b", "text": "updated"}]
        result = add_messages(old, new)
        assert [m["id"] for m in result] == ["a", "b", "c"]
        assert result[1] == {"id": "b", "text": "updated"}

    def test_remove_message_by_id(self):
        old = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
        new = [RemoveMessage(id="b")]
        assert add_messages(old, new) == [{"id": "a"}, {"id": "c"}]

    def test_remove_nonexistent_id_is_noop(self):
        old = [{"id": "a"}]
        new = [RemoveMessage(id="missing")]
        assert add_messages(old, new) == [{"id": "a"}]

    def test_remove_all_sentinel_clears_list(self):
        old = [{"id": "a"}, {"id": "b"}]
        new = [{"id": "c"}, REMOVE_ALL_MESSAGES, {"id": "d"}]
        # REMOVE_ALL wins; other items in the delta are ignored.
        assert add_messages(old, new) == []

    def test_none_old_treated_as_empty(self):
        assert add_messages(None, [{"id": "a"}]) == [{"id": "a"}]

    def test_empty_delta_returns_copy(self):
        old = [{"id": "a"}]
        result = add_messages(old, [])
        assert result == old
        assert result is not old  # new list

    def test_objects_with_id_attribute(self):
        old = [_Msg("a", "hi")]
        new = [_Msg("a", "updated"), _Msg("b", "new")]
        result = add_messages(old, new)
        assert result == [_Msg("a", "updated"), _Msg("b", "new")]

    def test_message_without_id_always_appends(self):
        old = [{"id": "a"}, {"text": "no id"}]
        new = [{"text": "also no id"}, {"id": "a"}]
        result = add_messages(old, new)
        # Un-keyed messages append; keyed "a" upserts in place.
        assert result == [
            {"id": "a"},
            {"text": "no id"},
            {"text": "also no id"},
        ]

    def test_type_error_on_non_list_old(self):
        with pytest.raises(TypeError, match="add_messages expects list"):
            add_messages({"not": "list"}, [])

    def test_type_error_on_non_list_new(self):
        with pytest.raises(TypeError, match="add_messages expects list"):
            add_messages([], "string")

    def test_remove_message_equality_and_hash(self):
        a = RemoveMessage(id="x")
        b = RemoveMessage(id="x")
        c = RemoveMessage(id="y")
        assert a == b
        assert a != c
        assert hash(a) == hash(b)
        assert hash(a) != hash(c)

    def test_remove_all_sentinel_is_singleton_like(self):
        assert REMOVE_ALL_MESSAGES is REMOVE_ALL_MESSAGES
        assert repr(REMOVE_ALL_MESSAGES) == "REMOVE_ALL_MESSAGES"


class TestDictMerge:
    def test_disjoint_keys_merge(self):
        assert dict_merge({"a": 1}, {"b": 2}) == {"a": 1, "b": 2}

    def test_right_wins_on_scalar_collision(self):
        assert dict_merge({"a": 1}, {"a": 2}) == {"a": 2}

    def test_recursive_merge_on_nested_dicts(self):
        old = {"outer": {"a": 1, "b": 2}}
        new = {"outer": {"b": 99, "c": 3}}
        assert dict_merge(old, new) == {"outer": {"a": 1, "b": 99, "c": 3}}

    def test_scalar_replaces_dict(self):
        assert dict_merge({"a": {"nested": 1}}, {"a": "scalar"}) == {"a": "scalar"}

    def test_dict_replaces_scalar(self):
        assert dict_merge({"a": "scalar"}, {"a": {"nested": 1}}) == {"a": {"nested": 1}}

    def test_does_not_mutate_inputs(self):
        old = {"a": {"b": 1}}
        new = {"a": {"c": 2}}
        old_copy = {"a": {"b": 1}}
        new_copy = {"a": {"c": 2}}
        dict_merge(old, new)
        assert old == old_copy
        assert new == new_copy

    def test_none_old_treated_as_empty(self):
        assert dict_merge(None, {"a": 1}) == {"a": 1}

    def test_type_error_on_non_dict(self):
        with pytest.raises(TypeError, match="dict_merge expects dict"):
            dict_merge([1, 2], {})
        with pytest.raises(TypeError, match="dict_merge expects dict"):
            dict_merge({}, [1, 2])

    def test_deep_nested_merge(self):
        old = {"a": {"b": {"c": 1}}}
        new = {"a": {"b": {"d": 2}}}
        assert dict_merge(old, new) == {"a": {"b": {"c": 1, "d": 2}}}
