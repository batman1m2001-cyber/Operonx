"""The application layer: what puts work into an Operon.

An Operon is compute. Everything that mints its runs lives here —

* :mod:`operonx.app.serve` — services: a listener, a session, the two
  door ops ``ingress`` and ``egress``;
* :mod:`operonx.app.jobs` — jobs: items, one run per item, every result
  kept in a record, an optional ``reduce``; and jobs of ``steps``, many
  jobs as one command;
* :mod:`operonx.app.evals` — evals: a dataset, a graph, evaluators — a
  job whose runs are ``origin=eval`` and whose record holds verdicts;
* :mod:`operonx.app.manifest` — ``operonx.toml``: the project, its
  services, ``[tracing]``, and ``[project] app`` pointing at the Python
  declaration;
* :class:`Application` — the declaration, with ``serve()``, ``run()``
  and ``describe()``: what production and the studio call. Services are
  declared in Python (`Service`) or in ``operonx.toml``; jobs and evals
  only in Python (`Job`, `Eval`).

The dependency runs one way. Graphs never import this package.
"""

from .application import Application, GraphRef
from .declare import Listener, Service, asgi, env, http, schedule, webhook, websocket
from .evals import Dataset, Eval
from .jobs import Job
from .manifest import Manifest, ManifestError, ServeSpec

__all__ = [
    "Application",
    "Dataset",
    "Eval",
    "GraphRef",
    "Job",
    "Listener",
    "Manifest",
    "ManifestError",
    "ServeSpec",
    "Service",
    "asgi",
    "env",
    "http",
    "schedule",
    "webhook",
    "websocket",
]
