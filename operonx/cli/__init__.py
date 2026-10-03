"""Operonx command-line entry points.

Renamed from ``operonx.tools`` in 1.2.0 — ``tools`` now reads as *agent
tools* (the ``@tool``-decorated callables an LLM invokes), so the CLI
namespace gave up the name to avoid a permanent clash with
``operonx.agents``.

There is one command, ``operonx`` (:mod:`operonx.cli.main`), with a
subcommand each:

- ``operonx init`` — a project laid out the way
  ``operonx/guide/05-project-layout.md`` says (:mod:`operonx.cli.init`);
- ``operonx guide`` — print or sync the guide for coding assistants;
- ``operonx serve`` / ``operonx run`` — serve the application's services,
  run its jobs (:mod:`operonx.cli.serve`, :mod:`operonx.cli.run`);
- ``operonx play`` — the playground bridge: drive a service's doors
  (:mod:`operonx.app.play`);
- ``operonx eval`` — run experiments, compare and report them, size them
  (:mod:`operonx.cli.eval`).

``run``, ``serve`` and ``play`` are each their module's own
``main(argv)``. The ``operonx-run`` / ``-serve`` / ``-play`` scripts are
deprecated aliases of them, kept for one release
(:mod:`operonx.cli.aliases`). ``operonx pack``, which serialised graphs
for the dropped Rust runtime, was removed in 1.15.

(An ``operonx = "operonx.cli:main"`` entry existed from the April 2026
Hush→Operon migration through 1.1.0, pointing at a scaffolding CLI that
was deleted in that same migration; it never resolved and was removed in
1.2.0. The command returned with ``init`` and ``guide``.)
"""
