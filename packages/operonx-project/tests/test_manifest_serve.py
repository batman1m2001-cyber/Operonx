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
    with pytest.raises(ManifestError, match="not declared"):
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
