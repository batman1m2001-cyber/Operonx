"""Project conventions, manifest, and graph extraction for operonx.

Runs inside a target project's own environment. Deliberately dependency-
minimal — see ``pyproject.toml``.
"""

from operonx_project.manifest import (
    MANIFEST_NAME,
    GraphSpec,
    Manifest,
    ManifestError,
    ResourceSpec,
    ServeSpec,
)

__version__ = "0.1.0"

__all__ = [
    "MANIFEST_NAME",
    "GraphSpec",
    "Manifest",
    "ManifestError",
    "ResourceSpec",
    "ServeSpec",
    "__version__",
]
