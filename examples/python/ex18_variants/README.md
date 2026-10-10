# 18 · Variants — one door, one compiled graph per caller kind

```
                              [serve.variants]
                       ┌── formal ── greet(style=formal, sign_off="Regards") ──► Operon
 POST /greet?variant=… ┤
                       └── casual ── greet(style=casual, sign_off="Cheers")  ──► Operon
```

A door whose graph differs by caller — a callbot with one turn graph per
agent, an API with one pipeline per tenant tier — used to have two bad
options: one graph with a "which am I" input threaded through every op,
or one `[[serve]]` per kind on separate paths. `[serve.variants]` is the
third: the door's `graph` is one module-level `@graph` whose per-variant
parts are its parameters, each variant binds them, `operonx serve` compiles one engine per variant at boot, and
the request picks one with `?variant=` — before its parameters are bound,
since variants may take different ones.

## The pieces

```
src/greet/
  graph.py     @graph greet(style, sign_off)         the graph the manifest names
  ops.py       greeting                              the element op
  styles.py    formal, casual                        what the variants bind
app.py         APP = Application(...) — the door, the graph, the variants, in one place
```

```python
# app.py — the application, in Python; operonx.toml only points at it
APP = Application(
    "ex18-variants",
    services=[
        Service(
            "greet",
            http("POST", "/greet", port=env("HTTP_PORT", 8018)),
            graph=greet,  # @graph greet(style, sign_off)
            variants={
                "formal": dict(style=styles.formal, sign_off="Regards"),
                "casual": dict(style=styles.casual, sign_off="Cheers"),
            },
        ),  # ?variant=formal|casual picks one; formal when absent
    ],
)
```

```toml
[project]
src = ["src", "."]  # import roots: packages under src/, app.py at the root
app = "app:APP"     # the declaration above
```

The same door written in TOML — `[[serve]] graph = "greet.graph:greet"`
with a `[serve.variants]` table of `module:attr` strings — is equivalent
and still works; the Python form is what a reader of the project sees.
A request that names no variant gets the first one declared (`formal`).
One the door does not have is refused at the door (`400`, `"field":
"variant"`) — no run is minted.

## Run it

```bash
uv sync
uv run operonx serve --list
#   ex18-variants
#     0.0.0.0:8018
#       greet          http       /greet           -> greet.graph:greet  [per_request]
#         [formal] style=greet.styles:formal sign_off=Regards
#         [casual] style=greet.styles:casual sign_off=Cheers
uv run operonx serve
curl -s -X POST 'localhost:8018/greet?variant=formal' -d '"ada lovelace"'   # "Good day, Ada Lovelace. Regards."
curl -s -X POST 'localhost:8018/greet?variant=casual' -d '"Ada"'            # "hey ada! Cheers."
curl -s -X POST 'localhost:8018/greet?variant=shouty' -d '"Ada"'            # 400: refused at the door
```

From Python, `Application.find().graphs` lists `greet[formal]` and
`greet[casual]` as two graphs under one service, each compilable on its
own — which is how the studio draws them.
