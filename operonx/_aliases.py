"""``operonx.agents`` and ``operonx.kb``: the separately installed packages, by a short name.

``from operonx.agents import Agent`` is ``from operonx_agents import Agent``, and every submodule
follows (``operonx.agents.testing`` *is* ``operonx_agents.testing``, the same module object, so
classes and ``isinstance`` agree whichever name imported them). operonx does not depend on
either package; importing one that is not installed raises ``ImportError`` naming its pip
install.
"""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.util
import sys
from typing import Dict, Optional, Tuple

__all__ = ["ALIASES", "install"]

#: short name → (real package, distribution to install)
ALIASES: Dict[str, Tuple[str, str]] = {
    "operonx.agents": ("operonx_agents", "operonx-agents"),
    "operonx.kb": ("operonx_kb", "operonx-kb"),
}


class _AliasFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def _target(self, fullname: str) -> Optional[Tuple[str, str]]:
        for short, (real, dist) in ALIASES.items():
            if fullname == short or fullname.startswith(short + "."):
                return real + fullname[len(short) :], dist
        return None

    def find_spec(self, fullname, path=None, target=None):
        return importlib.util.spec_from_loader(fullname, self) if self._target(fullname) else None

    def create_module(self, spec):
        real, dist = self._target(spec.name)
        try:
            return importlib.import_module(real)
        except ModuleNotFoundError as exc:
            if exc.name == real.split(".")[0]:  # the package itself, not something it imports
                raise ImportError(
                    f"{spec.name} is the {dist} package, which is not installed: "
                    f"pip install {dist}"
                ) from exc
            raise

    def exec_module(self, module) -> None:  # the real package already ran
        return None


def install() -> None:
    """Put the alias finder first on ``sys.meta_path`` (once)."""
    if not any(isinstance(f, _AliasFinder) for f in sys.meta_path):
        sys.meta_path.insert(0, _AliasFinder())
