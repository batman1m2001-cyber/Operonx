# 18 · Variants — one door, one compiled graph per caller kind

```
                              [serve.variants]
                       ┌── formal ── build(style=formal, sign_off="Regards") ──► Operon
 POST /greet?style=… ──┤
                       └── casual ── build(style=casual, sign_off="Cheers")  ──► Operon
                                                       ▲
                                    on_session names one: RunRequest(variant="casual")
```

A door whose graph differs by caller — a callbot with one turn graph per
agent, an API with one pipeline per tenant tier — used to have two bad
options: one graph with a "which am I" input threaded through every op,
or one `[[serve]]` per kind on separate paths. `[serve.variants]` is the
third: the door's `graph` is a **factory**, each variant binds its
parameters, `operonx-serve` compiles one engine per variant at boot, and
`on_session` picks with `RunRequest.variant`.

## The pieces

```
src/greet/
  graph.py     build(style, sign_off) -> @graph      the factory the manifest names
  ops.py       greeting                              the element op
  styles.py    formal, casual                        what the variants bind
  door.py      open(session) -> RunRequest(variant)  the pick
```

```toml
[project]
src = ["src"]                       # import roots; operonx puts them on sys.path

[[serve]]
graph = "greet.graph:build"         # a plain function, not a @graph
on_session = "greet.door:open"
[serve.variants]
formal = { style = "greet.styles:formal", sign_off = "Regards" }
casual = { style = "greet.styles:casual", sign_off = "Cheers" }
```

A bound value that reads as `module:attr` is loaded; anything else is a
literal. A `@graph` named where a factory is expected is refused with a
message that says so. A session that names no variant, or one the door
does not have, is refused at the door — no run is minted.

## Run it

```bash
uv sync
uv run operonx-serve --list
#   ex18-variants
#     0.0.0.0:8018
#       greet          http       /greet           -> greet.graph:build  [per_request]
#         [formal] style=greet.styles:formal sign_off=Regards
#         [casual] style=greet.styles:casual sign_off=Cheers
uv run operonx-serve
curl -s -X POST 'localhost:8018/greet?style=formal' -d '"ada lovelace"'   # "Good day, Ada Lovelace. Regards."
curl -s -X POST 'localhost:8018/greet?style=casual' -d '"Ada"'            # "hey ada! Cheers."
curl -s -X POST 'localhost:8018/greet?style=shouty' -d '"Ada"'            # refused at the door
```

From Python, `Application.find().graphs` lists `build[formal]` and
`build[casual]` as two graphs under one service, each compilable on its
own — which is how the studio draws them.
