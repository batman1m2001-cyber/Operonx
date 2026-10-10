# The playground bridge

A service is built to be called by a client — a phone gateway, a web
page, another service. Trying it by hand should not need that client.
The **playground bridge** drives a service's doors from outside: a small
process, started in the project's own interpreter, that speaks JSON lines
on stdin and stdout. The studio's Playground is one front end for it;
anything that can write a line and read a line is another.

```bash
operonx play --root path/to/project        # or: python -m operonx.app.play --root …
```

Nothing here is a second way to run a service. A playground session goes
through the same gate as production — the query bound to the graph's
parameters, its variants, its refusals (named: `{"t": "refused", "reason",
"field"}`) — the same door ops and the same graph. Only the origin differs: `origin=playground`, filed apart and
kept 7 days ([Runs](11-runs.md#retention)).

## The protocol

One JSON object per line; an `id` on a request comes back on its answer.

```json
{"op": "describe"}
{"op": "open", "sid": "s1", "service": "chat", "query": {"user": "42"}, "send": [{"kind": "text", "text": "hi"}]}
{"op": "send", "sid": "s1", "msg": {"kind": "text", "text": "and tomorrow?"}}
{"op": "end",  "sid": "s1"}
```

The bridge answers with events, each with a `t`: `ready` (once, at
start), `doors` (what `describe` found: each service, its transport, its
graph's inputs, its variants, its codec and toys), `opened`, `refused` (with a reason), `out` (an item
the door sent), `ops` (the op executions that finished since the last
batch — `{"op", "status", "ms"}` each — so a canvas can follow the run
live), `ended` (status, error, duration) and `error`.

## Codecs: toy messages to door items

Toys speak one small protocol — `{"kind": "text", "text": …}`,
`{"kind": "json", "value": …}`, `{"kind": "bytes", …}`,
`{"kind": "audio", "b64": …}` — and a door speaks whatever it speaks. A
**codec** translates: `to_door` turns a toy message into an ingress item,
`from_door` an egress item into a toy message.

http and websocket doors have built-in codecs (`JsonCodec` for a form,
`TextCodec` for a chat). A door whose protocol is its own declares one:

```python
from operonx.app.play import Codec, PcmCodec

class TelcoCodec(Codec):
    toys = ("voice", "chat")
    audio = {"rate": 8000, "encoding": "pcm16", "frame_ms": 40}

    def to_door(self, message):
        ...                      # a toy message → what the ingress op reads

    def from_door(self, item):
        ...                      # what the egress op sent → a toy message

Service("call", websocket("/ws/call"), graph=call_graph, playground=TelcoCodec)
```

(`playground = "module:attr"` in a deprecated `[[serve]]` block does the same.) A
door with no codec offers no toy. A session hook can tell a playground
session apart by `session.meta["playground"]`.

**Voice.** A codec with `audio` set offers the Voice toy: the toy sends
16-bit mono PCM at that rate in short batches, and `to_door_items` cuts a
batch into the door's own frames. `PcmCodec(rate=16000, frame_ms=20)`
covers a door that takes raw PCM frames. What the door sends that is not
audio — a transcript, an event — still reaches the toy.

## A worse world: conditions

`open` takes `conditions`, applied to that session only:

| Key | Effect |
|---|---|
| `latency_ms` | a delay before each inbound item |
| `drop` | the share of inbound items lost (0–1) |
| `noise_dbfs` | white noise mixed into audio |
| `silence_ms` | quiet audio before the first message |
| `fail` | resource keys that raise, for this session only (`["llm:main"]`) |

A failing resource is wrapped once and fails only where a session asked
it to; every other session, playground or not, is untouched.

## A simulated user

```json
{"op": "simulate", "sid": "s2", "service": "chat", "persona": "a busy parent who wants to reschedule",
 "llm": "llm:persona", "turns": 6, "first": "user"}
```

An LLM persona plays the other side: it waits for the service to go
quiet, reads the conversation, answers in character, and ends the session
when the persona says so. Each line it sends comes back as a `said`
event, and the run records it like any session.

## Where a session is recorded

A playground run goes to the service's **local** consumers only — files
and run stores in the project — so a test session never lands in a
production Langfuse. `"remote": true` on `open` sends it everywhere the
service traces to. The run carries what the toy sent, and when, so a
session reads back as a conversation and can be replayed.

## Replaying real sessions

A playground session keeps what the toy sent, so it can be replayed. A
real client's session can keep the same, when its service opts in:

```python
Service("summary", http("/summary"), graph=summary_graph, replay=True)
```

(or `replay = true` in a deprecated `[[serve]]` block). Each run of that door then
carries what the client sent, in order and stamped (`replay_script`), and
the connection's query (`replay_query`). On Monday a request crashes; on
Tuesday you fix the code and replay that very request in the playground.

- **Text and JSON are kept; audio and bytes are only counted.** A door
  whose JSON frames carry audio overrides its codec's `to_toy` to say
  which ones are audio, and those are counted too. So is any message over
  64 KB.
- **Off by default.** A script holds what users typed, and lives as long
  as its run (see [Retention](11-runs.md#retention)).

## Re-running one op

```json
{"op": "rerun", "service": "chat", "op_name": "reply", "inputs": {...}, "of": "<run id>"}
{"op": "rerun", "job": "score_calls", "op_name": "scored", "inputs": {...}}
```

One op of a service's graph (or a job's, compiled as the job compiles
it), run with the inputs it had in a recorded run: a failing op deep
inside a call retried in a second, without making the call. It is
recorded as a run of its own, with `rerun_of` pointing at the original.

## Key ops

`Service(..., key_ops=["stt", "llm_classify", "synthesize"])` names the
ops whose latency a team watches first — time to first audio, the LLM
call. `describe_service` carries them, and a dashboard pins them above
the rest.

## Where to go next

- What happens to the runs afterwards: [Runs](11-runs.md).
- Judge a graph against a dataset, in CI: [Evals](13-evals.md).
