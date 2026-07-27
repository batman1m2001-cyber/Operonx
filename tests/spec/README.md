# Shared JSON fixtures

These fixtures are duplicated in
[operonx-rs](https://github.com/batman1m2001-cyber/operonx-rs)
under the same `tests/spec/` path. Both projects run the same
`graph.json` / `inputs.json` / `expected.json` through their own
runtime and assert byte-equal output (modulo `$start_time` /
`$end_time` / `$duration_ms`).

## Sync policy

When adding or changing a fixture, apply the same change in both
repos in the same PR. The duplication is deliberate — it keeps each
project self-contained (no git submodule, no cross-repo build hop),
at the cost of one extra paste on cross-runtime work.

Files per fixture folder:

- `graph.json` — the serialized graph (both runtimes read this).
- `inputs.json` — kwargs passed to `engine.run(inputs=...)`.
- `expected.json` — golden output.
- `scratch.json` — optional, seeds `engine.run(scratch=...)`.
- `builder.py` — Python-side builder that regenerates `graph.json`
  via `operonx-pack`. Present in this repo only.
