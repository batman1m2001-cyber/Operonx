"""An op's outputs come from its source as it was imported, not as the file is now.

A graph builds its ops when it first runs, maybe long after import. Read from
disk then, a file edited or pulled in between (a deploy before the restart)
gave ``inspect.getsource`` the lines of other code at the compiled line
numbers: the op declared no outputs and its run failed with
``(<op>, hits) not found in schema`` (operonx-kb's graph search, 2026-11-23).
"""

import importlib.util
import inspect
import os
import sys

SOURCE = '''from operonx import op


def helper(n):
    return n * 2


@op
def walked(seed: list, k: int):
    """Hits from a seed."""
    return {"hits": seed[:k], "stats": {"k": k}}
'''


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_outputs_survive_an_edit_of_the_file_after_import(tmp_path):
    path = tmp_path / "edited_ops.py"
    path.write_text(SOURCE)
    module = _load(path, "edited_ops_snapshot")
    try:
        # Three lines above the op, as an edit or a git pull leaves them; the
        # mtime moves on so linecache sees a changed file.
        path.write_text(
            SOURCE.replace("from operonx import op\n", "from operonx import op\n# a\n# b\n# c\n")
        )
        stat = path.stat()
        os.utime(path, (stat.st_atime + 5, stat.st_mtime + 5))

        node = module.walked(seed=[1, 2, 3], k=2)

        assert set(node.outputs) == {"hits", "stats"}
        assert node(seed=[1, 2, 3], k=2) == {"hits": [1, 2], "stats": {"k": 2}}
    finally:
        sys.modules.pop("edited_ops_snapshot", None)


def test_the_kept_source_is_what_inspect_reads_from_an_unchanged_file(tmp_path):
    path = tmp_path / "plain_ops.py"
    path.write_text(SOURCE)
    from operonx.core.ops.transform.func_op import _source_of

    module = _load(path, "plain_ops_snapshot")
    try:
        fn = module.walked.__wrapped__
        assert fn._op_source_lines  # kept at decoration
        assert _source_of(fn) == inspect.getsource(fn)
    finally:
        sys.modules.pop("plain_ops_snapshot", None)
