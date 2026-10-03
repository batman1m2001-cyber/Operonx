# Spec fixtures

Golden-output tests: each folder builds a graph, runs it with fixed
inputs and compares the result with a stored answer, so a change in what
the engine computes shows up as a diff of a JSON file.

Files per fixture folder:

- `builder.py` — `build_graph() -> GraphOp`, using the shared ops in
  `_ops.py`. Its presence is what makes a folder a fixture.
- `inputs.json` — kwargs passed to `engine.run(inputs=...)`.
- `expected.json` — golden output (timing keys stripped).
- `scratch.json` — optional, seeds `engine.run(scratch=...)`.

`scripts/regen_fixture.py <fixture dir>` rewrites `expected.json` from
the builder. Review the diff before committing it: a golden that changed
is a behaviour change.

(These fixtures were once shared with the operonx-rs runtime, which read
a serialised `graph.json` from each folder. That runtime is dropped, and
the `graph.json` files went with it.)
