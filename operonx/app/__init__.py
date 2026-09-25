"""The application layer: what puts work into an Operon.

An Operon is compute. Everything that mints its runs lives here —

* :mod:`operonx.app.serve` — services: a listener, a session, the two
  door ops ``ingress`` and ``egress``;
* :mod:`operonx.app.jobs` — jobs: a source, one run per item, a sink, a
  record; and runbooks, many jobs as one command;
* :mod:`operonx.app.manifest` — ``operonx.toml``, where both are declared;
* :class:`Application` — the loaded manifest, with ``serve()``, ``run()``
  and ``describe()``: what production and the studio call.

The dependency runs one way. Graphs never import this package.
"""

from .application import Application, GraphRef
from .manifest import JobSpec, Manifest, ManifestError, ServeSpec

__all__ = ["Application", "GraphRef", "JobSpec", "Manifest", "ManifestError", "ServeSpec"]
