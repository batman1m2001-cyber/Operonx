# 9. Seeing a project: operonx-studio

`operonx-studio` is a local web app for an operonx project. It draws every
graph as a canvas, lets you run ops in a playground, and shows runs, evals,
jobs and services. It is a separate tool, not a dependency of your project:
install it once, outside the project, and point it at any project.

## Install

From a clone of its repository:

```bash
git clone https://github.com/batman1m2001-cyber/operonx-studio
operonx-studio/install.sh             # again after a `git pull` to upgrade
```

It installs `operonx-studio` (and `operonx-lint`, `operonx-extract`,
`operonx-new`) as a uv tool, in an environment of its own, or with plain
pip when uv is missing. Without bash (Windows): `pip install ./operonx-studio`.

## Open a project

```bash run
operonx init myapp        # a project to look at (any operonx project works)
```

```bash
cd myapp
uv sync                   # the studio reads the project with the project's own .venv
uv run operonx studio     # starts the studio, adds the project, opens it in the browser
```

With pip, set the project up as its `AGENTS.md` says (a `.venv`, then
`pip install -e ".[test]"`), and run `operonx studio` in the activated `.venv`.

`operonx studio` finds the project at or above the current folder. If no
studio is running, it starts one (Ctrl+C stops it). If one is already
running on the port, it adds the project there and returns at once.
`operonx-studio .` does the same.

- **Sign in:** the first sign-in is `root` / `123`, and the studio asks for a
  new password right away.
- **Its own state:** accounts and conversations live in
  `OPERONX_STUDIO_STATE_DIR` (default `~/.operonx`), not in the project.
- **Other projects:** `operonx-studio` with no path opens a home screen to
  pick, open or create one.

The studio never imports your project into its own process. It reads each
graph in a subprocess under the project's `.venv` (or its own Python when
the project has none) and redraws when you save a file. A project that
fails to import shows the error instead of a canvas.

## Reading the canvas

The **Flow** page has two views; `D` switches between them.

- **Workflow** shows what runs after what: ops, branches, loops (a violet
  zone), nested graphs you can open in place, and the serve doors.
- **Data Flow** shows which value goes where.

On either view:

- **Click an op** to draw all its data wires. Each wire is a faint
  hairline with a spark running the way the data goes.
- **Point at a variable** (its pill) or at a wire to single it out.
- **Click a variable** to follow its value through the graph.

A value read by several ops splits at one point. An input fed by
alternative branches meets at one merge ring.

The **?** button on the canvas explains every mark; `/` finds an op.

## The other pages

| Page | What it is for |
|---|---|
| **Playground** | talk to the project's graph turn by turn, as a user of the service would |
| **Runs** | every recorded run: its tree, each op's inputs and outputs |
| **Monitor**, **Alerts** | latency and errors over time, and the rules that warn on them |
| **Review**, **Evals** | label outputs; read each eval run case by case (run evals with `operonx eval`, see [Evals](07-evals.md)) |
| **Services**, **Jobs** | start and stop the project's services, run its jobs |

## The project commands it brings

```bash
operonx-lint              # lint operonx.toml and the graphs
operonx-lint --build .    # … and build every graph offline
operonx-extract           # the project's graphs as JSON (what the canvas draws)
```
