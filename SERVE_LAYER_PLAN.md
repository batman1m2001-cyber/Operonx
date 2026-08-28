# Serve layer — the transport belongs inside the graph's world

Status: **in progress.** 2026-08-29. Steps 1-4 built — manifest parser,
transport protocol, registry, in-memory transport, `ingress`/`egress` and
the session runner, with the third-party-transport gate passing. Steps 5-8
(the `http` and `websocket` built-ins, the `asgi` mount, the CLI, and the
callbot migration) remain.

Building it surfaced a data-loss bug in the transient-ports work this
plan depends on — a chain of three ops lost every item but the first —
fixed in 821b37a. Two notes recorded below under "What building it
changed".

## The finding

operonx already has two serve layers. Neither one works, and neither
knows the other exists.

**One.** `Operon.serve()` at `operonx/core/engine.py:928` takes `path`,
`host`, `port`, `websocket` and `backend` as arguments and delegates to
`operonx.serve.OperonApp`:

```python
try:
    from operonx.serve import OperonApp
except ImportError:
    raise ImportError("operonx-serve is required for engine.serve() ...")
```

`operonx-serve` is not installed, not a dependency in `pyproject.toml`,
and not a package in this repository. Every call to `engine.serve()`
raises. It is a stub in front of something that does not exist.

**Two.** `operonx.toml` declares `[[serve]]` — and *nothing in operonx
core parses `operonx.toml` at all*. It is read only by the external
tooling (`operonx-lint`, `operonx-extract`, `operonx-studio`), purely as
description. The callbot's manifest says so plainly:

```toml
[[serve]]
kind  = "websocket"
path  = "/ws/call"
graph = "pipeline"
```

with a comment explaining exactly why it had to be written by hand:

> What puts work into a graph. Nothing derived from the graph can say
> this: uvicorn calls an ASGI route, which calls `engine.start()`, and
> that hop is not an op. Without it the studio draws a pipeline that
> begins from nowhere.

So the manifest already declares precisely what `engine.serve()` accepts
as parameters, and the two have never been connected.

**That reframes this work.** It is not "build a serving framework". It is
"make the declaration that already exists be the thing that runs", and
delete the 437 lines of `server/ws_server.py` that currently stand in the
gap.

## Why it matters beyond tidiness

A graph plus a manifest should be a deployable product. Today a graph is
half a product and the other half is hand-written per project — and the
hand-written half is where this month's bugs were. Fault injection
against the callbot on 2026-08-28 found two, both in that seam:

* `customer_info` parsed with a `JSONDecodeError` guard, which is the
  wrong half — `[1,2,3]`, `"hello"`, `42`, `true` and `null` all parse
  cleanly, are not objects, and each killed the call at accept.
* nothing bounded a script field between the query string and a TTS POST,
  so a long name drew a 422 and every turn of that call fell silent.

Neither is exotic. Both are the kind of thing a serving layer does once,
correctly, instead of every project doing it again by hand.

## The shape that has to be got right

A transport is not a pipe of items. It is a thing that **mints runs**, and
the interesting variable is how many runs per connection:

| shape             | runs per connection | duplex             |
|-------------------|---------------------|--------------------|
| `POST /predict`   | 1                   | reply to caller    |
| file / batch      | N                   | write out          |
| queue worker      | 1 per message       | maybe              |
| **WS callbot**    | **1, long-lived**   | **same socket**    |

The first three are served by a channel pair. The callbot is not: the
socket is a *session* that mints one run and stays bound to it, carrying
per-run state — `vad_state`, `call_over`, `websocket` — that belongs to
the connection rather than to any item.

Design for the session case and request/response falls out as the
degenerate form. The reverse does not, which is why the existing
`engine.serve(websocket=True)` stub — request/response shaped — would not
have carried the callbot even if the package it points at existed.

## The design

### Transport is an extension point, not a wrapper

This is the centre of the whole thing. WebSocket and HTTP are **built-in
implementations of an open interface**, not the interface itself. A
project writes its own transport in its own code the same way it writes
its own ops, and nothing about it is second-class.

```python
class Session(Protocol):
    meta: dict                                   # query, headers, filename…
    def recv(self) -> AsyncIterator[Any]: ...    # items in
    async def send(self, item: Any) -> None: ... # items out
    async def close(self) -> None: ...

class Transport(Protocol):
    def sessions(self) -> AsyncIterator[Session]: ...
```

That is the contract. A SIP trunk, a Kafka consumer, a serial port, a
directory watcher, an in-house RPC — each is a class implementing two
methods, registered exactly the way this project already registers a
thread pool or an HTTP endpoint:

```python
REGISTRY.register(SipTrunkConfig, _create_sip_trunk)
```

```toml
[[serve]]
kind = "sip_trunk"                  # a registered name…
kind = "my_company.transports:SipTrunk"   # …or an import path
```

Built-ins ship for `websocket`, `http`, `asgi`, `file` and `queue`
because most projects want them and nobody should write a WebSocket
handshake twice. They earn no privileges for it: they register through
the same call, are selected by the same `kind`, and can be replaced
without touching operonx.

### Transport binds through ops

```python
recv = Ingress(resource="serve:call")
...
Egress(resource="serve:call", inputs={"frame": played["frame"]})
```

`Ingress` drives `session.recv()`; `Egress` calls `session.send()`. Not
`serve(in_channel, out_channel)` as the primary mechanism, for the reason
the callbot's audio path was rewritten into edges this month: keeping the
transport visible in the graph buys real spans, sweepable contexts and
per-item tracing, where an async seam buys none of it. The one-in-one-out
`engine.serve()` sugar should compile down to these two ops rather than
being a second path with its own semantics.

### The manifest is the single source

`operonx.toml` stops being description and becomes what boots. Multiple
endpoints, grouped onto listeners by port:

```toml
schema = 2

[project]
name = "educa_reminder"

[resources]
overlay = "resources.yaml"

[[serve]]
name         = "call"
kind         = "websocket"
path         = "/ws/call"
port         = "${WS_API_PORT:9922}"
graph        = "pipeline.graph:ws_callbot_pipeline"
session      = "per_connection"
max_inflight = 4000
on_session   = "server.call_context:open_call"

[[serve]]
name    = "transcribe"
kind    = "http"
method  = "POST"
path    = "/v1/transcribe"
port    = "${HTTP_API_PORT:9923}"
graph   = "pipeline.graph:asr_flow"
session = "per_request"

[[serve]]
name = "admin"
kind = "asgi"
path = "/"
port = "${HTTP_API_PORT:9923}"
app  = "server.http_api:app"
```

Four decisions are folded into that file.

**`[[graph]]` goes away.** It existed to name entry points so tooling
could find them, and `[[serve]]` already names one. The callbot's manifest
lists three graphs and then admits in its own comment that two of them are
subgraphs nothing serves — they are there for the studio's benefit. A
graph is walkable; subgraphs are reachable from the root. Keep `[[graph]]`
only for graphs genuinely unreachable from any endpoint, which most
projects will not have.

**`[graph.inputs]` fixtures go away.** The callbot ships a fake student,
a fake hotline and a sample Vietnamese utterance inside its deployment
descriptor, because lint and studio needed something to run with. That is
test data. It belongs in the test suite, or in a `[[fixture]]` block that
`operonx serve` ignores.

**`${VAR:default}` substitution comes to the manifest.** Ports move per
environment — 9922 production, 9924 dev, 9926 staging — so the structural
half (`kind`, `path`, `graph`, `session`) lives in the manifest and the
environmental half arrives through the same substitution `resources.yaml`
already uses. This is what keeps `serve` in one file instead of splitting
it across two.

**`kind = "asgi"` mounts foreign apps.** The callbot's second server is
health, customer records and call summaries — CRUD over files, no graph
run. It is not a graph and should not pretend to be one, but the manifest
should still describe the whole product. So `operonx serve` owns the
process and mounts an existing ASGI app at a path.

### `serve` stays out of `resources.yaml`

`resources.yaml` is the resource hub: what the project *reaches out to* —
LLM gateway, STT, TTS, ONNX models, thread pools, trace sinks — resolved
by the hub into objects that ops call. Outbound.

`operonx.toml` is what the project *is*. Inbound. No op ever calls
`hub.get("serve:call")`, and putting serving config in the hub would make
the hub mean two different things.

### One honest seam for project logic

```toml
on_session = "server.call_context:open_call"
```

```python
def open_call(conn) -> RunRequest:
    return RunRequest(inputs={...}, scratch={...}, trace_id=conn.query["call_id"])
```

Turning `?call_id=&customer_info=` into `script_data`, refusing an unknown
`agent_type`, hitting the customer store — that is project logic and no
amount of TOML will express it. Every serving framework that pretended
otherwise grew a plugin system instead. One declared hook, and the
framework stops guessing.

## Decisions

**A run always finishes; a transport never cancels it.** This is not a new
rule — it is what the callbot already does, and the reason it has no
cancel-versus-finish race to resolve. The socket closing is not an
external event: `receive_audio` *is* an op holding the socket, so a
disconnect surfaces as that generator ending, its `finally` sets
`call_over`, downstream ops drain, and the run completes on its own.
`ws_server` only awaits the monitor task; nothing outside cancels
anything. Three edge scenarios cover it —
`hangup_during_greeting`, `immediate_disconnect`, `disconnect_mid_reply`.
It matters that this stays the default: `terminal_events` writes the CRM
record *after* the caller has already gone, and a framework that killed
the run on disconnect would silently lose every hangup's record.
**Ingress ending is the only end-of-input signal.** A transport wanting
cancellation cancels the run explicitly.

**Failure-to-response follows from `session`, with no extra config.**
`per_request` maps an unhandled op failure to 5xx — there is exactly one
caller waiting and a 200 with an empty body is the worst possible answer.
`per_connection` logs and continues, because a phone call must survive a
bad turn. The session mode already carries the information, so it decides.

**Backpressure defaults per kind, and never drops silently.** Stream
transports (`websocket`, `file`, `queue`) stop reading at `max_inflight`;
for TCP that is the socket's own flow control, which is both correct and
free. Request transports answer 503. Every instance is counted and
logged. `max_inflight` is mandatory in the manifest rather than optional,
because `seq_queues` is an unbounded deque today — transient ports fixed
retention, not volume — and an unbounded queue behind a network socket is
exactly how the Channels came to exist in the first place.

**The session lives in the run context, not SCRATCH.** The callbot puts
the socket in SCRATCH today and it works, but the binding is invisible to
anything reading the graph. `Ingress` and `Egress` resolve their session
from the run that the transport minted.

**`schema = 2`.** Three external tools parse this file. A stamp means a
stale `operonx-studio` says "this manifest is newer than me" rather than
"this project has no graphs" — a loud failure instead of a silently wrong
answer. Absent means version 1.

**Built-in transports ship as an extra, `operonx[serve]`.** FastAPI and
uvicorn should not become core dependencies for users who never serve, or
who bring their own transport. Custom transports need nothing installed
beyond operonx itself.

## Sequence

1. **Manifest parser in core**, reading `schema = 2` with `${VAR}`
   substitution. No behaviour — just parse and validate. The current
   format keeps working.
2. **The `Transport`/`Session` protocol and the registry hook**, with an
   in-memory transport as the first implementation and the only one used
   by tests.
3. **`RunRequest` and the `on_session` hook** — the vocabulary for "a
   connection became a run".
4. **`Ingress`/`Egress` ops**, resolving their session from the run
   context, tested against the in-memory transport.
5. **`http` built-in, `session: per_request`** — the simple shape, and
   the one that exercises the failure-to-5xx mapping.
6. **`websocket` built-in, `session: per_connection`** — the hard shape.
7. **`asgi` mount**, and `operonx serve` as a CLI entry.
8. **Migrate the callbot** and delete what is left of `ws_server.py`.

Steps 2-4 are the actual product. If a third party cannot write a working
transport against what exists after step 4, without reading operonx's
source, the interface is wrong and steps 5-6 will cement the mistake.

## Gates

- The full existing suite, at every step. This adds a layer; nothing
  already working may move.
- `engine.serve()` either works or is removed. Leaving a stub that raises
  `ImportError` in front of a real serving layer is worse than either.
- A custom transport, written against the public protocol only, drives the
  callbot pipeline end to end. This is the gate that proves the extension
  point is real rather than decorative — and it should be written before
  the WebSocket built-in, not after, so the built-in cannot quietly become
  the interface.
- The callbot is the acceptance test, because it is the hardest shape:
  **`server/ws_server.py` goes from 437 lines to under 100**, with
  `tests/test_edge_cases.py` at 16/16 and `tests/test_fault_injection.py`
  at 22/22, both unchanged.
- The two bugs fault injection found — non-object `customer_info`, and an
  unbounded field reaching TTS — must be impossible to write in a project
  built on this layer, not merely fixed again inside it.

## What this is really for

The point is not that operonx gains a WebSocket server. It is that the
boundary of a graph stops being the boundary of operonx. Whatever puts
work in and takes results out — a telco socket, a Kafka topic, a
directory of files, a protocol nobody outside your company has heard of —
is written against one small interface and is then a first-class part of
the run: traced, bounded, swept and ended like everything else.

The built-in HTTP and WebSocket transports exist so that the common cases
need no code. They are the floor, not the ceiling.


## What building it changed

**`ingress` and `egress` need no `resource=`.** The plan had them naming a
serve entry. They do not have to: the run was minted by a transport, so it
already carries its session, and `current_session()` finds it. One fewer
thing to wire, one fewer thing to get wrong — and when there is no session
the same graph still runs under a plain `engine.start()`, which is what
keeps an `ingress`-bearing graph testable without a server.

**The bound lives in the session, not the ingress op.** `BoundedSession`
holds it, because that is where items enter: when the buffer is full the
transport stops reading its socket, and for TCP that is the connection's
own flow control — applied before anything is allocated inside the graph,
and free. An op-side bound would have had to guess when a consumer was
finished with an item, which is exactly the question the release guard
above got wrong.

**`max_inflight` is required from schema 2, not from schema 1.** A parser
that refuses to read the manifests it was written to understand is no use;
existing files keep working and get a warning instead.
