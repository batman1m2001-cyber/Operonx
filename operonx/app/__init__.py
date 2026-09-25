"""The application layer: what puts work into an Operon.

An Operon is compute. Everything that mints its runs lives here —

* :mod:`operonx.app.serve` — services: a listener, a session, the two
  door ops ``ingress`` and ``egress``;
* :mod:`operonx.app.jobs` — jobs: a source, one run per item, a sink, a
  record; and runbooks, many jobs as one command;
* :mod:`operonx.app.manifest` — ``operonx.toml``, where both are declared;
* :class:`Application` — the declaration, with ``serve()``, ``run()``
  and ``describe()``: what production and the studio call. Declared in
  Python (`Service`, `Job`) or parsed from ``operonx.toml``; the file
  can also just point at the Python object with ``[project] app``.

The dependency runs one way. Graphs never import this package.
"""

from .application import Application, GraphRef
from .declare import Listener, Service, asgi, env, http, websocket
from .manifest import JobSpec, Manifest, ManifestError, ServeSpec

__all__ = [
    "Application",
    "GraphRef",
    "JobSpec",
    "Listener",
    "Manifest",
    "ManifestError",
    "ServeSpec",
    "Service",
    "asgi",
    "env",
    "http",
    "websocket",
]
