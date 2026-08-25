"""Plan and apply a typed edit to a project, addressed the way the UI sees it.

The UI knows a graph by its **manifest label** (``callbot.ahamove_hr``) and a
node by its short name. The editors in :mod:`pyedit` work on source text and
a ``@graph`` name. This module is the bridge, and it exists so the studio
never has to know where a graph lives on disk.

Nothing is written until :func:`apply_plan` is called. A plan carries the
unified diff, so the change can be shown before it happens — which is what
"code is the source of truth" has to mean in practice: the file changes only
after someone has seen exactly how.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from operonx_project.manifest import Manifest, ManifestError
from operonx_project.pyedit import (
    delete_op,
    insert_op_after,
    insert_op_between,
    rename_op,
    set_op_resource,
)

__all__ = ["EditPlan", "plan_edit", "apply_plan", "ACTIONS", "PlanError"]


class PlanError(Exception):
    """The edit cannot be addressed to a file, or is not a known action."""


@dataclass(frozen=True)
class EditPlan:
    """A pending edit: what file, what it becomes, and how it differs."""

    file: Path
    graph: str
    action: str
    before: str
    after: str

    @property
    def changed(self) -> bool:
        return self.before != self.after

    @property
    def diff(self) -> str:
        """Unified diff, empty when the edit is a no-op."""
        if not self.changed:
            return ""
        return "".join(
            difflib.unified_diff(
                self.before.splitlines(keepends=True),
                self.after.splitlines(keepends=True),
                fromfile=str(self.file),
                tofile=str(self.file),
            )
        )


# The typed vocabulary. Anything outside it is an assistant diff, not a
# structural edit — a UI must not invent an action the tools cannot verify.
ACTIONS: Dict[str, Callable[..., str]] = {
    "rename": rename_op,
    "set_resource": set_op_resource,
    "delete": delete_op,
    "insert_after": insert_op_after,
    "insert_between": insert_op_between,
}


def _module_file(manifest: Manifest, module: str) -> Path:
    """Locate a module's source file under the project's declared roots."""
    relative = Path(*module.split(".")).with_suffix(".py")
    for entry in manifest.src or (".",):
        candidate = (manifest.root / entry / relative).resolve()
        if candidate.exists():
            return candidate
    package = (manifest.root / relative.with_suffix("") / "__init__.py").resolve()
    if package.exists():
        return package
    raise PlanError(f"cannot locate source for module {module!r} under {list(manifest.src)}")


def plan_edit(manifest: Manifest, graph_label: str, action: str, **kwargs: Any) -> EditPlan:
    """Work out what an edit would do, without touching the file.

    Raises:
        PlanError: unknown action, or the graph cannot be located.
        PyEditError: the edit does not apply — the op is missing, a name
            collides, a resource is computed rather than literal.
    """
    if action not in ACTIONS:
        raise PlanError(f"unknown action {action!r}; known: {', '.join(sorted(ACTIONS))}")
    try:
        spec = manifest.graph(graph_label)
    except ManifestError as exc:
        raise PlanError(str(exc)) from exc

    module, attr = spec.entry.split(":")
    path = _module_file(manifest, module)
    before = path.read_text(encoding="utf-8")
    after = ACTIONS[action](before, attr, **kwargs)
    return EditPlan(file=path, graph=graph_label, action=action, before=before, after=after)


def apply_plan(plan: EditPlan, expected: Optional[str] = None) -> bool:
    """Write a plan to disk. Returns False when nothing changed.

    ``expected`` guards against the file having moved under us between
    planning and applying — the daemon reloads on every save, and a user
    editing in their editor at the same time is normal, not exotic.

    Raises:
        PlanError: the file changed since the plan was made.
    """
    if not plan.changed:
        return False
    current = plan.file.read_text(encoding="utf-8")
    baseline = expected if expected is not None else plan.before
    if current != baseline:
        raise PlanError(
            f"{plan.file} changed since this edit was planned; re-plan against "
            f"the current file rather than overwriting someone else's work"
        )
    plan.file.write_text(plan.after, encoding="utf-8")
    return True
