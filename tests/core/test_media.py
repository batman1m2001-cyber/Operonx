"""Tests for the Media primitive and extraction helpers."""

from operon.core.media import (
    Media,
    MediaRef,
    extract_media,
    substitute_placeholder,
)


class TestMediaPrimitive:
    def test_construct_and_read(self):
        m = Media(data=b"abc", mime_type="image/png")
        assert m.data == b"abc"
        assert m.mime_type == "image/png"

    def test_frozen(self):
        m = Media(data=b"abc", mime_type="image/png")
        try:
            m.mime_type = "image/jpeg"  # type: ignore[misc]
        except Exception:
            return
        raise AssertionError("Media should be frozen")


class TestMediaRefFromMedia:
    def test_bytes_size(self):
        m = Media(data=b"0123456789", mime_type="audio/mp3")
        ref = MediaRef.from_media(m, field_path="inputs.audio")
        assert ref.size_bytes == 10
        assert ref.mime_type == "audio/mp3"
        assert ref.field_path == "inputs.audio"

    def test_string_size(self):
        m = Media(data="data:image/png;base64,AAA=", mime_type="image/png")
        ref = MediaRef.from_media(m, field_path="inputs.image")
        assert ref.size_bytes == len("data:image/png;base64,AAA=")


class TestExtractMedia:
    def test_top_level_field(self):
        io = {"audio": Media(data=b"blob", mime_type="audio/mp3")}
        stripped, refs = extract_media(io, "inputs")
        assert stripped == {"audio": "<media:0>"}
        assert len(refs) == 1
        assert refs[0].field_path == "inputs.audio"
        assert refs[0].data == b"blob"

    def test_nested_dict(self):
        io = {"payload": {"image": Media(data=b"x", mime_type="image/png")}}
        stripped, refs = extract_media(io, "inputs")
        assert stripped == {"payload": {"image": "<media:0>"}}
        assert refs[0].field_path == "inputs.payload.image"

    def test_nested_list_with_indices(self):
        img = Media(data=b"p", mime_type="image/png")
        io = {
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "text"}, {"type": "image_url", "image_url": img}],
                }
            ]
        }
        stripped, refs = extract_media(io, "inputs")
        assert len(refs) == 1
        assert refs[0].field_path == "inputs.messages[0].content[1].image_url"
        content = stripped["messages"][0]["content"]
        assert content[1]["image_url"] == "<media:0>"

    def test_multiple_media(self):
        io = {
            "a": Media(data=b"1", mime_type="image/png"),
            "b": Media(data=b"22", mime_type="image/jpeg"),
        }
        stripped, refs = extract_media(io, "outputs")
        assert len(refs) == 2
        assert stripped["a"] == "<media:0>"
        assert stripped["b"] == "<media:1>"
        assert refs[0].size_bytes == 1
        assert refs[1].size_bytes == 2

    def test_no_media(self):
        io = {"x": 1, "y": "hello", "z": [1, 2, 3]}
        stripped, refs = extract_media(io, "inputs")
        assert refs == []
        assert stripped == io


class TestSubstitutePlaceholder:
    def test_top_level(self):
        io = {"audio": "<media:0>"}
        assert substitute_placeholder(io, "inputs.audio", "@@@REF@@@")
        assert io == {"audio": "@@@REF@@@"}

    def test_nested(self):
        io = {"payload": {"image": "<media:0>"}}
        assert substitute_placeholder(io, "inputs.payload.image", "@@@REF@@@")
        assert io["payload"]["image"] == "@@@REF@@@"

    def test_list_index(self):
        io = {"messages": [{"content": [{"type": "image_url", "image_url": "<media:0>"}]}]}
        path = "inputs.messages[0].content[0].image_url"
        assert substitute_placeholder(io, path, "@@@REF@@@")
        assert io["messages"][0]["content"][0]["image_url"] == "@@@REF@@@"

    def test_missing_path_returns_false(self):
        io = {"audio": "<media:0>"}
        assert not substitute_placeholder(io, "inputs.nope", "x")
