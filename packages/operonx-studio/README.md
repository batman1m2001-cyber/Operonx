# operonx-studio

Visual workspace for operonx projects. Reads the Project IR produced by
[`operonx-project`](../operonx-project) and renders it.

```bash
operonx-studio path/to/project -o studio.html --open
```

One self-contained HTML file — no CDN, no server, no network calls. It works
on a machine with no internet, which is usually where a workflow needs
looking at.

## What the view shows

- **Every node**, including orphans. A diagram that quietly omits things is
  worse than no diagram.
- **Edges only between nodes that exist.** `START`/`END` are boundaries and
  are drawn as pins on the nodes, never invented as phantom nodes — that was
  the "arrows out of nowhere" failure of earlier attempts.
- **Where each input comes from**: the producing op and output, a `SCRATCH`
  key, or a literal. That is the question a graph is opened to answer.
- **Who softened an edge**: an author's `~` reads differently from one the
  compiler auto-softened at a branch merge.
- **Rewritten cycles**, announced. Cycle rewriting deletes back-edges from
  the built graph, so they are shown from the rewrite record instead of
  silently disappearing.

## Layout

Layered (Sugiyama-lite) and computed in Python, not the browser: the same IR
always draws the same picture, so a diff means something, and it is testable
without a headless browser.

Cycles are excluded from layering. A cyclic edge has no forward layer to
advance to, and longest-path over one does not terminate.

## Live reload

```bash
pip install operonx-studio[serve]
operonx-studio path/to/project --serve --open
```

Serves the same page on loopback and reloads the browser when any watched
file changes — `.py`, `.toml`, `.yaml`, `.env`.

**Extraction runs in a subprocess, never in the daemon's own process.** Two
constraints force it:

- Extraction imports the project's modules. Re-extracting in the same
  interpreter would hand back the *cached* module, so the page would show
  stale structure while claiming to be live — exactly the class of lie the
  viewer exists to avoid.
- `ResourceHub._instance` is a class-level singleton and top-level module
  names collide across projects, so one process handles one project.

It also means a project that raises on import shows the traceback in the
page instead of killing the server, and recovers by itself once the code
builds again.

Change detection is mtime polling rather than a filesystem-watch library:
one fewer dependency, works the same over a network mount, and at these
sizes a scan is cheaper than the extraction it guards.

A statically written file never polls — only the served page gets the reload
hook, so a shared HTML file does not chase a daemon that is not there.

## Later

Editing is P3.
