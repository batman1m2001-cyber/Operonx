# 05 — Loops & Branches (Rust)

Generator ops + `if_()` branch routing, mirroring the Python side.

| Scenario     | Ops                          | Status        |
|--------------|------------------------------|---------------|
| `for_loop`   | `each_item → process_item`   | runs (limited)|
| `map_op`     | `each_number → square`       | runs (limited)|
| `while_loop` | `halve_until`                | runs (limited)|
| `branch`     | `if_() → [excellent/…/fail]` | not run yet   |

## Rust-runtime limitations

- **Generator ops** — the Rust streaming scheduler does not yet dispatch
  generator `yield`s per item. `each_item`, `each_number`, and
  `halve_until` return the collected list as a single-shot value
  instead, so downstream ops see the whole list rather than one item
  per frame. Output diverges from the Python side.
- **`if_()` branching** — `OpType::Branch` is stubbed in the Rust
  scheduler. The `branch` scenario is excluded from `main.rs` until
  branch dispatch lands; the `#[op]` bodies still ship so regen
  parity holds.

## Project layout

```
ex05_loops_and_branches/
├── Cargo.toml         # operonx + inventory + serde_json
├── README.md
├── src/main.rs        # generator + branch #[op] declarations
├── graph.json
└── inputs.json
```

## Run

```bash
cargo run --release
```

## Authoring graph specs

`graph.json` was generated from the matching Python builders. To
regenerate after editing Python ops, see `tools/dump-graph.py` at the
repo root.
