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
        _write(tmp_path, '\n[[serve]]\nkind = "websocket"\npath = "/ws/call"\ngraph = "pipeline"\n')
    )
    assert m.serves == (ServeSpec(kind="websocket", graph="pipeline", path="/ws/call"),)
    assert m.serves[0].as_dict() == {
        "kind": "websocket",
        "graph": "pipeline",
        "path": "/ws/call",
    }


def test_cron_carries_a_schedule_and_no_path(tmp_path):
    m = Manifest.load(
        _write(tmp_path, '\n[[serve]]\nkind = "cron"\nschedule = "0 3 * * *"\ngraph = "pipeline"\n')
    )
    assert m.serves[0].as_dict() == {
        "kind": "cron",
        "graph": "pipeline",
        "schedule": "0 3 * * *",
    }


def test_several_entry_points_on_one_graph(tmp_path):
    """A graph can be reachable more than one way."""
    m = Manifest.load(
        _write(
            tmp_path,
            '\n[[serve]]\nkind = "websocket"\npath = "/ws/call"\ngraph = "pipeline"\n'
            '\n[[serve]]\nkind = "http"\npath = "/v1/run"\ngraph = "pipeline"\n',
        )
    )
    assert [s.kind for s in m.serves] == ["websocket", "http"]


def test_unknown_graph_is_a_manifest_error(tmp_path):
    """A typo must not render as a graph served by nothing."""
    with pytest.raises(ManifestError, match="neither a `module:function`"):
        Manifest.load(
            _write(tmp_path, '\n[[serve]]\nkind = "websocket"\ngraph = "ppeline"\n')
        )


def test_unknown_kind_is_a_manifest_error(tmp_path):
    with pytest.raises(ManifestError, match="unknown kind"):
        Manifest.load(
            _write(tmp_path, '\n[[serve]]\nkind = "carrier-pigeon"\ngraph = "pipeline"\n')
        )


@pytest.mark.parametrize("missing, body", [
    ("kind", '\n[[serve]]\ngraph = "pipeline"\n'),
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


def test_the_older_form_still_works(tmp_path):
    """Naming a [[graph]] keeps working — projects have these files today."""
    m = _write_whole(tmp_path, """
[project]
name = "demo"

[[graph]]
name  = "pipeline"
entry = "demo.pipeline:main"

[[serve]]
kind  = "websocket"
graph = "pipeline"
""")
    assert [g.name for g in m.graphs] == ["pipeline"]
    assert m.serves[0].graph == "pipeline"


def test_both_forms_in_one_manifest(tmp_path):
    m = _write_whole(tmp_path, """
[project]
name = "demo"

[[graph]]
name  = "extra"
entry = "demo.extra:build"

[[serve]]
kind  = "websocket"
graph = "extra"

[[serve]]
kind  = "http"
path  = "/v1/asr"
graph = "demo.pipeline:asr_flow"
""")
    assert sorted(g.name for g in m.graphs) == ["asr_flow", "extra"]


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
