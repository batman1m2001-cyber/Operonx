# Hush-ai TODO

## Known Issues

- **Example 10 (Multi-Model) Rust serve**: `Ref.apply()` with callable is not supported in Rust mode. The Rust backend fails to parse graph config for endpoints using `Ref.apply()`. Workaround: use `@op(rust="...")` instead. Affects `/routing`, `/balanced`, `/fallback`, `/ensemble` endpoints.

## Backlog

- Support `Ref.apply()` in Rust mode (rush-core) — requires serializing Python callables or converting to Rust transform chains
