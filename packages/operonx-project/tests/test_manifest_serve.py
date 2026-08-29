"""`[[serve]]` — what puts work into a graph.

The IR's nodes, edges and entries are all derived from the graph. Nothing
derived can say what *calls* it: the hop from an ASGI route to
`engine.start()` is not an op and cannot be made one. So it is declared,
and these tests pin the two things a declaration has to get right — it
records what was written, and it rejects what cannot be true.
"""

from __future__ import annotations

import pytest
from operonx_project import Manifest, ManifestError, ServeSpec

pytestmark = pytest.mark.unit

BASE = """
[project]
name = "demo"

[[graph]]
name = "pipeline"
entry = "mod:build"
"""


def _write(tmp_path, body):
    (tmp_path / "operonx.toml").write_text(BASE + body, encoding="utf-8")
    return tmp_path


def test_absent_serve_is_fine(tmp_path):
    """A project that serves nothing declares nothing."""
    m = Manifest.load(_write(tmp_path, ""))
    assert m.serves == ()


def test_websocket_round_trips(tmp_path):
    m = Manifest.load(
        _write(tmp_path, '\n[[serve]]\nkind = "websocket"\npath = "/ws/call"\ngraph = "mod:build"\n')
    )
    assert m.serves == (ServeSpec(kind="websocket", graph="build", path="/ws/call"),)
    assert m.serves[0].as_dict() == {
        "kind": "websocket",
        "graph": "build",
        "path": "/ws/call",
    }


def test_a_custom_transport_is_named_by_import_path(tmp_path):
    """A project's own transport must be as loadable as a built-in.

    `kind` used to be checked against a fixed list, so a manifest operonx
    serves happily would not lint, would not extract and would not draw —
    the extension point was invisible to every tool.
    """
    m = Manifest.load(
        _write(tmp_path, '\n[[serve]]\nkind = "my_co.transports:SipTrunk"\ngraph = "mod:build"\n')
    )
    assert m.serves[0].kind == "my_co.transports:SipTrunk"
    assert m.serves[0].graph == "build"


def test_a_kind_nothing_implements_is_still_refused(tmp_path):
    """`cron` and `queue` were listed as known while nothing served them.

    A manifest naming one linted clean and then failed at boot, which is
    the worst order for those two things to happen in.
    """
    with pytest.raises(ManifestError, match="unknown kind"):
        Manifest.load(
            _write(tmp_path, '\n[[serve]]\nkind = "cron"\ngraph = "mod:build"\n')
        )


def test_several_entry_points_on_one_graph(tmp_path):
    """A graph can be reachable more than one way."""
    m = Manifest.load(
        _write(
            tmp_path,
            '\n[[serve]]\nkind = "websocket"\npath = "/ws/call"\ngraph = "mod:build"\n'
            '\n[[serve]]\nkind = "http"\npath = "/v1/run"\ngraph = "mod:build"\n',
        )
    )
    assert [s.kind for s in m.serves] == ["websocket", "http"]


def test_a_graph_that_is_not_an_entry_point_is_a_manifest_error(tmp_path):
    """A bare name used to mean "look it up in [[graph]]". It no longer does."""
    with pytest.raises(ManifestError):
        Manifest.load(
            _write(tmp_path, '\n[[serve]]\nkind = "websocket"\ngraph = "pipeline"\n')
        )


def test_unknown_kind_is_a_manifest_error(tmp_path):
    with pytest.raises(ManifestError, match="unknown kind"):
        Manifest.load(
            _write(tmp_path, '\n[[serve]]\nkind = "carrier-pigeon"\ngraph = "mod:build"\n')
        )


@pytest.mark.parametrize("missing, body", [
    ("kind", '\n[[serve]]\ngraph = "mod:build"\n'),
    ("graph", '\n[[serve]]\nkind = "http"\n'),
])
def test_required_fields(tmp_path, missing, body):
    with pytest.raises(ManifestError, match=f"missing '{missing}'"):
        Manifest.load(_write(tmp_path, body))


# ── serve names its own graph ───────────────────────────────────────────
# `[[graph]]` existed to give entry points names so `[[serve]]` could
# refer to them. Once serve names one directly, that indirection is a
# second place to keep in step for no benefit.

def _write_whole(tmp_path, body: str):
    """A complete manifest, not BASE plus a fragment.

    These cases are about what a manifest may leave out, so they cannot
    build on a BASE that already declares a `[[graph]]`.
    """
    (tmp_path / "operonx.toml").write_text(body, encoding="utf-8")
    return Manifest.load(tmp_path)


def test_serve_may_name_an_entry_point_with_no_graph_block(tmp_path):
    m = _write_whole(tmp_path, """
[project]
name = "demo"

[[serve]]
kind  = "websocket"
path  = "/ws/call"
graph = "demo.pipeline:main"
""")
    # The graph is synthesised, so lint, extract and the studio still see a
    # GraphSpec and need no change of their own.
    assert [g.name for g in m.graphs] == ["main"]
    assert m.graphs[0].entry == "demo.pipeline:main"
    assert m.serves[0].graph == "main"
    assert m.graph("main").entry == "demo.pipeline:main"


def test_two_serves_on_one_entry_point_synthesise_one_graph(tmp_path):
    m = _write_whole(tmp_path, """
[project]
name = "demo"

[[serve]]
kind  = "http"
path  = "/a"
graph = "demo.pipeline:main"

[[serve]]
kind  = "http"
path  = "/b"
graph = "demo.pipeline:main"
""")
    assert len(m.graphs) == 1
    assert [s.graph for s in m.serves] == ["main", "main"]


def test_graph_blocks_remain_for_graphs_nothing_serves(tmp_path):
    """The sixteen examples in this repo are all of this shape.

    `[[graph]]` lost its job of naming entry points for `[[serve]]`. It
    keeps the one it always had: making a graph nobody serves visible to
    lint, extract and the studio.
    """
    m = _write_whole(tmp_path, """
[project]
name = "demo"

[[graph]]
name  = "experiment"
entry = "demo.lab:try_this"

[[serve]]
kind  = "websocket"
graph = "demo.pipeline:main"
""")
    assert sorted(g.name for g in m.graphs) == ["experiment", "main"]
    assert m.serves[0].graph == "main"


def test_asgi_mounts_an_app_and_needs_no_graph(tmp_path):
    """Health, CRUD and admin routes are not graphs and must not need one.

    This kind was rejected outright before, so a manifest describing a
    whole deployment could not be loaded by the tools at all.
    """
    m = _write_whole(tmp_path, """
[project]
name = "demo"

[[serve]]
kind  = "websocket"
graph = "demo.pipeline:main"

[[serve]]
kind = "asgi"
path = "/"
app  = "demo.admin:app"
""")
    assert [s.kind for s in m.serves] == ["websocket", "asgi"]
    assert m.serves[1].graph == ""
    assert [g.name for g in m.graphs] == ["main"]


def test_a_malformed_entry_point_is_caught_at_parse(tmp_path):
    with pytest.raises(ManifestError):
        _write_whole(tmp_path, """
[project]
name = "demo"

[[serve]]
kind  = "websocket"
graph = "demo.pipeline:"
""")
