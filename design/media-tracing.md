# Media Attachment Tracing

**Status:** proposed
**Target:** `python/hush-icore/hush/core/` (new `media.py`, edits to `tracing/collector.py` and `states/ref.py`); `python/hush-telemetry/hush/telemetry/tracers/*`; `python/hush-providers/hush/providers/ops/llm.py`
**Motivation:** multimodal I/O (images, audio, video) is currently mishandled across all three tracer backends. Base64 blobs either bloat Langfuse payloads or get silently dropped by OTEL. A single lightweight primitive fixes this for LLM vision, STT, TTS, OCR, image generation, and any future media-carrying op.

## Problem

### Today's behavior with multimodal I/O

1. **Collector** ([collector.py:305-340](python/hush-icore/hush/core/tracing/collector.py#L305-L340)) reads `inputs` / `outputs` from state verbatim. If the state holds an OpenAI chat-format message containing `{"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}`, the full base64 blob sits inside `node.inputs`.
2. **Langfuse tracer** ([langfuse.py:187-188, 233-234](python/hush-telemetry/hush/telemetry/tracers/langfuse.py#L187-L188)) passes `node.inputs` / `node.outputs` straight into the ingestion body. A 500KB PNG becomes a ~670KB JSON blob inside a single `generation-create` event. The Langfuse UI shows it as a text blob, not a previewable image, and trace payloads push against the 1MB/event and 5MB/batch limits.
3. **OTEL tracer** ([otel.py:160-178](python/hush-telemetry/hush/telemetry/tracers/otel.py#L160-L178)) serializes I/O as a JSON string attribute but **silently drops** anything over 10KB. Base64 images always exceed 10KB, so multimodal inputs just disappear from OTEL spans with no warning.
4. **hush-eyes** (local tracer, HTTP POST to `ui-hush-eyes`): no detection either.

### Custom ops (STT, TTS, vision, image-gen, OCR)

A `@op` that consumes or produces audio/image/video today has **no** way to tell the collector "this field holds media." The op just returns `{"audio": b"..."}` or `{"image": "data:image/png;base64,..."}` and the tracer mangles it.

## Design principles

1. **Producer-side marker, consumer-side transparency.** The op that creates media wraps it in a `Media` instance once, at the source. Every downstream op reads the raw value (`bytes`, `str`) with no knowledge of the wrapper. Zero `Media` imports in consumer ops.
2. **One primitive, not a type system.** Exactly one new public symbol (`Media`), one dataclass, ~15 lines. Schemas stay raw (`Param(type=bytes)`), not `Param(type=Media)`.
3. **Fix at the collector, dispatch at the tracer.** Detection logic lives in one place. Each tracer backend handles upload however it prefers.
4. **No Rust backend impact.** Wrapping happens at op-return time, unwrapping happens at Ref resolution time. The Rust execution backend only ever sees resolved raw values; serialization is untouched.

## The `Media` primitive

```python
# python/hush-icore/hush/core/media.py
from dataclasses import dataclass

@dataclass(frozen=True)
class Media:
    """Wrapper marking a value as media for trace capture.

    Producer ops wrap their output once:

        return {"audio": Media(synthesize(text), mime_type="audio/mp3")}

    Consumer ops read the raw value — Hush auto-unwraps at Ref resolution,
    so schemas stay plain (``Param(type=bytes)``) and no ``Media`` import
    is needed in downstream code.
    """
    data: bytes | str                   # raw bytes OR a data URL / URL / path
    mime_type: str                      # "image/png", "audio/mp3", ...
```

Deliberately two fields. No `filename` — it's cosmetic, usually absent (TTS
and image-gen have no natural name), and can be added backwards-compatibly
later if a real need appears. Producers who want to preserve a user-uploaded
name carry it as a sibling output key (``{"image": Media(...), "source_name":
"cat.jpg"}``).

## Execution model

### Producer

```python
@op
def tts(text: str):
    return {"audio": Media(synthesize(text), mime_type="audio/mp3")}
```

### Consumer

```python
@op
def upload(audio: bytes):              # schema type = bytes, NOT Media
    s3.put_object(Body=audio, ...)
```

### Schema declaration

```python
input_schema = {"audio": Param(type=bytes, required=True)}
output_schema = {"audio": Param(type=bytes)}
```

No `Param(type=Media)` anywhere. The wrapper is invisible to schema validation.

### Graph

```python
with GraphOp(name="pipeline") as g:
    speech = tts(text=PARENT["query"])
    back = stt(audio=speech["audio"])    # bytes, not Media
    START >> speech >> back >> END
```

## Two read paths, one state store

The core trick is that state keeps the `Media` wrapper intact on write, but **Ref resolution strips it on read for consumer ops**. The collector uses a different, raw read path that preserves the wrapper.

| Read path | Who uses it | Returns |
|---|---|---|
| **Ref resolution** (`op["audio"]` → consumer input binding) | Engine when binding Refs to downstream op inputs | `media.data` — the raw bytes / string |
| **Raw state read** (`state[op_name, var, ctx]`) | Collector, debugging, serialization | the `Media` instance as-is |

Concretely, the Ref resolution path adds one check:

```python
# python/hush-icore/hush/core/states/ref.py (or wherever Ref.apply reads state)
value = state[source_op, source_key, ctx]
if isinstance(value, Media):
    value = value.data
return value
```

The collector's walk in [collector.py:310-315](python/hush-icore/hush/core/tracing/collector.py#L310-L315) already uses `state[op_name, v, ctx]` directly, **not** through Ref resolution, so it sees the wrapper automatically.

## Collector extraction

After `normalize_trace_io()`, the collector walks each trace I/O dict recursively, finds `Media` instances, and pulls them into a parallel `node.media` list with placeholders left in the I/O:

```python
# Before extraction
trace_inputs = {"audio": Media(data=b"<48kb>", mime_type="audio/mp3")}

# After extraction
trace_inputs = {"audio": "<media:0>"}
node.media   = [
    MediaRef(
        field_path="inputs.audio",
        data=b"<48kb>",
        mime_type="audio/mp3",
        size_bytes=48293,      # convenience; == len(data)
    ),
]
```

`MediaRef` is a sibling dataclass in `hush.core.media`.

### `field_path` format

A dotted JSONPath-like string the tracer uses to substitute the placeholder back after upload:

- Dict key access:     `inputs.audio`
- Nested dict:         `inputs.payload.image`
- List index:          `inputs.messages[0].content[1].image_url.url`
- Mixed:               `outputs.results[2].thumbnail`

Always rooted at `inputs` or `outputs` (never `extras` — those get the same treatment as the parent key since extras is just a nested dict). The tracer's substitution helper takes `(trace_io_dict, field_path, replacement)` and walks the path to apply the swap in-place.

## Per-op trace normalization hook

LLMOp is the one place where media arrives in a non-`Media` shape: users write OpenAI chat format directly inside the `messages` input:

```python
messages = [
    {"role": "user", "content": [
        {"type": "text", "text": "What's in this image?"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}},
    ]},
]
```

This value is written into state by the user / upstream op before LLMOp's code runs, so LLMOp cannot retroactively rewrite what the collector will read. Instead, `BaseOp` grows a trace-only hook that the collector calls between reading raw state and running `_extract_media()`:

```python
# python/hush-icore/hush/core/ops/base.py
class BaseOp:
    def normalize_trace_io(
        self, inputs: dict, outputs: dict
    ) -> tuple[dict, dict]:
        """Produce a trace-time view of this op's I/O.

        Called by the collector before media extraction. Return copies
        of inputs/outputs with any op-specific media shapes converted
        to ``Media`` instances. Default is identity — most ops never
        override.
        """
        return inputs, outputs
```

LLMOp overrides:

```python
# python/hush-providers/hush/providers/ops/llm.py
def normalize_trace_io(self, inputs, outputs):
    msgs = inputs.get("messages")
    if msgs:
        inputs = {**inputs, "messages": _wrap_openai_media_blocks(msgs)}
    return inputs, outputs
```

`_wrap_openai_media_blocks()` walks `messages[*].content[*]` and replaces recognized multimodal blocks with `Media` instances. The real state value — what downstream ops read — is untouched; the backend still sends the original OpenAI shape over the wire. Only the collector's copy carries the `Media` wrapping, and only so `_extract_media()` can find it.

Collector integration:

```python
# python/hush-icore/hush/core/tracing/collector.py
raw_inputs  = {v: state[op_name, v, ctx] for v in op.inputs  if not v.startswith("$")}
raw_outputs = {v: state[op_name, v, ctx] for v in op.outputs if not v.startswith("$")}
trace_inputs, trace_outputs = op.normalize_trace_io(raw_inputs, raw_outputs)
trace_inputs,  inputs_media  = _extract_media(trace_inputs,  prefix="inputs")
trace_outputs, outputs_media = _extract_media(trace_outputs, prefix="outputs")
node.media = inputs_media + outputs_media
```

Alternative considered: put OpenAI chat-format knowledge directly in the collector. Rejected — format-specific parsing belongs with the op that understands the format, not in the tracing layer. The hook pattern keeps the collector generic while letting each op opt in.

## Per-tracer media dispatch

After the collector strips media into `node.media[]`, each tracer gets a clean I/O dict plus a list of blobs to handle.

### Langfuse

Use Langfuse's media API:

1. For each `MediaRef`, `POST /api/public/media` with `contentType`, `contentLength`, `sha256Hash` → receive an upload URL and a `mediaId`.
2. `PUT` the raw bytes to the upload URL.
3. Substitute the placeholder in the node's I/O string with `@@@langfuseMedia:type=<mime>|id=<mediaId>|source=bytes@@@`.
4. Langfuse UI renders this token as a native previewable image/audio/video.

Implementation lives in the existing [client.py](python/hush-telemetry/hush/telemetry/backends/langfuse/client.py) (add an `upload_media()` method) and [langfuse.py](python/hush-telemetry/hush/telemetry/tracers/langfuse.py) tracer (call before `ingest()`).

### OTEL

Two options:

- **v1 (this plan):** drop the blob, set an attribute `llm.media.dropped_count=N` and `llm.media.total_bytes=M`, plus one attribute per blob with its mime type. Users at least know media existed and how big.
- **v2 (follow-up):** pluggable object-store uploader (S3 / GCS / Azure Blob) via `OTELConfig.media_store`, replace placeholder with returned URL in span attribute.

### hush-eyes

Add a `media` blob column on the existing trace node table. Store bytes inline (it's a local dev tool — size isn't a concern). UI reads the row and shows previews.

## Files touched

| File | Change |
|---|---|
| `python/hush-icore/hush/core/media.py` | **NEW** — `Media` and `MediaRef` dataclasses |
| `python/hush-icore/hush/core/__init__.py` | Export `Media` |
| `python/hush-icore/hush/core/ops/base.py` | Add default `normalize_trace_io()` hook on `BaseOp` |
| `python/hush-icore/hush/core/states/ref.py` | Add `Media` auto-unwrap in Ref resolution (~3 lines) |
| `python/hush-icore/hush/core/tracing/collector.py` | Call `normalize_trace_io()` + `_extract_media()`, populate `node.media` |
| `python/hush-icore/hush/core/tracing/models.py` | Add `media: list[MediaRef]` field on `TraceNode` |
| `python/hush-providers/hush/providers/ops/llm.py` | Override `normalize_trace_io()` to wrap OpenAI multimodal blocks |
| `python/hush-telemetry/hush/telemetry/backends/langfuse/client.py` | Add `upload_media()` method (multipart or presigned PUT) |
| `python/hush-telemetry/hush/telemetry/tracers/langfuse.py` | Upload media, substitute reference tokens by `field_path` before `ingest()` |
| `python/hush-telemetry/hush/telemetry/tracers/otel.py` | Drop-with-counter for v1 |
| `python/hush-telemetry/hush/telemetry/tracers/hush_eyes.py` | Pass media blobs inline |

## Non-goals

- **No new input/output parameter type.** Schemas use plain Python types (`bytes`, `str`, `dict`). `Media` never appears in a `Param(type=...)`.
- **No raw-bytes auto-detection.** If a user returns `{"audio": b"..."}` without wrapping in `Media`, the collector treats it as opaque bytes — trace may show truncated repr, but nothing breaks. Explicit wrapping is the contract.
- **No mime-type inference.** Producers must supply `mime_type`. LLMOp normalization parses it from data URL headers (`data:image/png;base64,...` → `image/png`). Custom ops pass the string directly.
- **No v2 object-store uploader for OTEL** in this plan. That's a follow-up once the v1 drop-with-counter ships.
- **No Rust-native media extraction.** Rust ops don't know about the Python `Media` type, so a Rust-only workflow's trace flow (if/when one goes directly from Rust state to a Rust-side flush) falls back to today's behavior — verbatim I/O, no extraction. v1 covers the Python collector path, which is what Python tracers (Langfuse, OTEL, hush-eyes) consume today. Rust-side media tracing is a follow-up.

## Risks

- **State serialization.** If state is persisted (checkpointing, debug dumps), a `Media` instance needs to round-trip. The `@dataclass(frozen=True)` is JSON-unfriendly by default — add a `to_dict()` / `from_dict()` pair, or register a custom JSON encoder that detects `Media` and emits `{"$media": true, "data": "...", "mime_type": "..."}`. Mitigation: we don't checkpoint state today, so this is a v2 concern, but the `to_dict()` helper is worth adding proactively.
- **Large blobs in memory.** If an op returns a 50MB video, the `Media.data` field holds 50MB in RAM until the collector flushes. This matches today's behavior — multimodal values are already held in state. Mitigation: `Media` supports `data: str` holding a URL or path so producers can pass large files by reference instead of bytes.
- **Langfuse upload failure.** If the media API is unreachable, we'd leak a placeholder into the trace with nothing to resolve it. Mitigation: on upload failure, log a warning and substitute a fallback placeholder like `[media upload failed: audio/mp3, 48KB]` so the trace still makes sense.
- **LLMOp normalization coverage.** Need to handle at least: `image_url` (OpenAI, Anthropic), `input_audio` (OpenAI realtime), Gemini `inline_data`. Missing any of these means that provider's multimodal calls won't get media extraction. Mitigation: v1 covers `image_url` + `input_audio`; add Gemini `inline_data` when we test against Gemini vision.

## Implementation steps

1. **Add `Media` primitive** in `hush.core.media`. Include `MediaRef` for collector output.
2. **`normalize_trace_io` hook**: add the default no-op method on `BaseOp`.
3. **Ref auto-unwrap**: find the Ref resolution site in `hush.core.states.ref` and add the `isinstance(v, Media)` strip (~3 lines).
4. **Collector walk**: update `_scan_nodes()` to call `op.normalize_trace_io(raw_inputs, raw_outputs)` then run `_extract_media()` on the result. Add `media: list[MediaRef]` field on `TraceNode`. Implement `_extract_media()` as a recursive walk producing `(stripped_dict, media_refs)` with `field_path` computed as described above.
5. **LLMOp override**: implement `LLMOp.normalize_trace_io()` that walks `messages[*].content[*]` and replaces `image_url` / `input_audio` blocks with `Media` instances. Parse `mime_type` from the data URL header.
6. **Langfuse**: add `upload_media()` to the REST client. Wire into the tracer before `ingest()` — walk `node.media[]`, upload each, substitute the placeholder at `field_path` in the trace I/O with the `@@@langfuseMedia:...@@@` token.
7. **OTEL**: drop-with-counter in the flush path.
8. **hush-eyes**: add media column + inline storage.
9. **Tests**:
   - Unit: `Media` round-trip through state, Ref auto-unwrap strips wrapper for consumers, collector extraction produces correct `field_path` for nested shapes.
   - Unit: `LLMOp.normalize_trace_io()` converts OpenAI vision `messages` into a Media-wrapped copy without mutating the original.
   - Integration: LLMOp vision call → Langfuse trace shows preview (manual verify against real Langfuse instance).
   - Integration: fake STT op returns `Media(wav_bytes, "audio/wav")` → trace contains extracted blob, downstream op receives raw bytes via auto-unwrap.
10. **Documentation**: one page in `docs/guide/` explaining when to use `Media` and when not to.
