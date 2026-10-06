"""Resource categories reach operonx through entry points (upstream U2)."""

import os
import subprocess
import sys
import textwrap

from operonx.core.registry import REGISTRY

from operonx_kb.registry import BlobStoreConfig, CatalogConfig, LexicalIndexConfig


def test_categories_are_registered_on_import():
    assert REGISTRY.get_class("kb_catalog") is CatalogConfig
    assert REGISTRY.get_class("kb_blob") is BlobStoreConfig
    assert REGISTRY.get_class("kb_lexical") is LexicalIndexConfig


def test_a_fresh_process_resolves_kb_keys_through_entry_points(tmp_path):
    (tmp_path / "resources.yaml").write_text(
        f"kb_catalog:main:\n  path: {tmp_path}/c.db\nkb_blob:main:\n  root: {tmp_path}/b\n",
        encoding="utf-8",
    )
    script = textwrap.dedent(
        """
        import sys
        from operonx.core.registry import ResourceHub
        hub = ResourceHub.from_yaml("resources.yaml")
        assert "operonx_kb" not in sys.modules
        print(type(hub.get("kb_catalog:main")).__name__, type(hub.get("kb_blob:main")).__name__)
        """
    )
    out = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.split() == ["SqliteCatalog", "LocalMediaStore"]
