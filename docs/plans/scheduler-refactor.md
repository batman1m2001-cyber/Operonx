# Task-Based Scheduler Refactor

## Vấn đề hiện tại

Scheduler hiện tại dùng event queue + 5 biến state riêng lẻ cho MỖI GraphOp. Khi có chained streaming (source → audio → vad), mỗi generator yield tạo stream context chiếm concurrency slot → slot hết → yields bị queue → deadlock.

```
Hiện tại: mỗi GraphOp tạo scheduler riêng

┌─ educa_workflow ──────────────────────────────────┐
│  Scheduler A (active_count, running_streams, ...) │
│                                                   │
│  detect → ┌─ classify ────────────────┐ → merge  │
│           │  Scheduler B (riêng!)     │           │
│           │  prompt → llm → parser    │           │
│           └───────────────────────────┘           │
└───────────────────────────────────────────────────┘

Vấn đề:
- Scheduler B chạy nested bên trong Scheduler A
- A chờ B xong → B chờ slot từ A → deadlock có thể xảy ra
- Cancel workflow? Phải cancel A + B + ... từng cái
- Debug? Phải trace xuyên qua nhiều scheduler
```

## Thiết kế mới

Một `WorkflowScheduler` duy nhất cho toàn bộ workflow execution.

```
┌─ WorkflowScheduler ──────────────────────────────────────┐
│                                                          │
│  pending: [ ]          ← tasks chờ chạy                  │
│  active:  { }          ← asyncio.Task đang chạy          │
│  sem:     Semaphore(64) ← giới hạn concurrent tasks       │
│                                                          │
│  Mọi op đều submit vào đây, kể cả ops trong subgraph    │
└──────────────────────────────────────────────────────────┘
```

GraphOp vẫn là op bình thường. Scheduler gọi `graphop.run()` → bên trong `run()` lấy scheduler chung (qua ContextVar) → submit child ops → await children xong → return result.

```
Scheduler dispatch "classify" (GraphOp)
  │
  ├→ classify.run() bắt đầu
  │     │
  │     ├→ submit prompt vào scheduler chung
  │     ├→ submit llm vào scheduler chung (chờ prompt xong)
  │     ├→ submit parser vào scheduler chung (chờ llm xong)
  │     │
  │     ├→ await children xong...
  │     │     (scheduler chung dispatch prompt → llm → parser song song với ops khác)
  │     │
  │     └→ return result
  │
  ├→ Scheduler nhận "classify done"
  └→ dispatch merge
```

## Main Loop

```
while có task pending HOẶC có task đang chạy:
    │
    ├── 1. Lấy tasks từ pending → chạy (respect semaphore)
    │
    ├── 2. await asyncio.wait(active_tasks, FIRST_COMPLETED)
    │       ↓
    │       Task A xong trước!
    │       │
    │       ├── Store result vào state
    │       ├── Tìm downstream ops đã ready (all inputs available)
    │       └── Enqueue downstream vào pending
    │
    └── 3. Generator yield? → tạo stream context → enqueue downstream

Done khi: pending rỗng + active rỗng
```

### So sánh

```
HIỆN TẠI                          MỚI
─────────────────────────         ─────────────────────────
5 biến state                      2 biến: pending + active
5 event types                     2 events: done + yield
event queue + polling             asyncio.wait(FIRST_COMPLETED)
per-GraphOp scheduler             1 scheduler cho cả workflow
running_streams counter           semaphore (tự quản lý)
stream context = slot             stream context = label only
nested scheduler deadlock         không nested → không deadlock
```

## Generator / Streaming

Generator op (async yield) chạy trong 1 task. Mỗi yield:
1. Store result vào state với stream context `ctx + ("[0]",)`, `("[1]",)`, ...
2. Tìm downstream ready → enqueue

```
source yields 3 chunks:

    source task (long-running)
    ├── yield chunk_0 → ctx=("main","[0]") → enqueue audio cho ctx [0]
    ├── yield chunk_1 → ctx=("main","[1]") → enqueue audio cho ctx [1]
    └── yield chunk_2 → ctx=("main","[2]") → enqueue audio cho ctx [2]

    Scheduler dispatch audio[0], audio[1], audio[2] song song
    (nếu semaphore cho phép)
```

Generator yield → scheduler nhận qua `yield_queue` (asyncio.Queue) → enqueue downstream. Generator task vẫn active cho đến khi exhausted.

### N-to-M Pattern (VAD)

```
audio yields 50 chunks → vad nhận 50 lần
vad yield 0 (PENDING) cho 49 chunks  ← scheduler không dispatch downstream
vad yield 1 speech segment           ← scheduler dispatch STT

    audio[0] → vad[0] → (no yield)
    audio[1] → vad[1] → (no yield)
    ...
    audio[49] → vad[49] → yield speech! → STT dispatched
```

Không cần stream context release. Vad task chạy xong (yield 0 hoặc 1) → task done → semaphore release. Stream context chỉ là label cho output collection.

## Loop Support

Loop = chạy cùng graph nhiều lần, carry forward outputs. Tích hợp trong scheduler:

```
@graph.loop(until="error == None", max_iterations=3)
def extract(error="init"):
    prompt → llm → parser

Iteration 1: error="init" → prompt → llm → parser → error="parse failed"
             until check: "parse failed" == None? NO → loop
Iteration 2: error="parse failed" → prompt → llm → parser → error=None
             until check: None == None? YES → STOP
```

Scheduler detect graph có `_loop_config` → sau mỗi iteration, eval `until` → nếu False, re-seed inputs từ outputs, chạy lại.

## Cancel / Abort

```python
scheduler.cancel()   # graceful: không enqueue task mới, chờ active xong
scheduler.abort()    # force: cancel tất cả active tasks ngay
```

Use case: user hangup giữa cuộc gọi → `scheduler.cancel()` → audio pipeline dừng nhận chunk mới, STT đang chạy thì chờ xong, trả partial result.

## Streaming Output (_output_queue)

`Hush.stream()` dùng `_output_queue` ContextVar để nhận real-time events. Scheduler mới vẫn put events vào queue:

```python
if output_queue is not None:
    await output_queue.put({"type": "token", "op": op_name, "data": result})
```

Giữ nguyên format, giữ nguyên ContextVar.

## Scheduler truyền xuống như thế nào

ContextVar — set 1 lần ở top-level, mọi GraphOp.run() bên trong lấy ra:

```python
_current_scheduler = contextvars.ContextVar("scheduler", default=None)

class WorkflowScheduler:
    async def run(self, graph, state, inputs):
        token = _current_scheduler.set(self)
        try:
            # execute graph
            ...
        finally:
            _current_scheduler.reset(token)

class GraphOp:
    async def run(self, state, ctx, parent_ctx):
        scheduler = _current_scheduler.get()
        if scheduler is None:
            raise RuntimeError(
                "No WorkflowScheduler found. "
                "GraphOp must run inside Hush engine: engine = Hush(graph); await engine.run()"
            )
        return await scheduler.execute_subgraph(self, state, ctx, parent_ctx)
```

## Implementation Phases

```
Phase 1: Core scheduler — batch ops (no streaming, no loop)
         Test: tests/ops/flow/, tests/ops/transform/, tests/test_workflow.py
         ↓
Phase 2: Streaming — generator yield + N-to-M
         Test: tests/ops/test_streaming.py (34 methods)
         ↓
Phase 3: Loops — until condition + carry forward
         Test: tests/ops/graph/test_graph_loop.py (20 methods)
         ↓
Phase 4: Concurrent + Engine — semaphore, _output_queue, nested graphs
         Test: tests/test_concurrent.py, tests/ops/test_engine_stream.py
         ↓
Phase 5: Integration + cancel/abort
         Test: callbot-engine-hush speech pipeline
```

Mỗi phase: implement → chạy tests → fix → next.

## Files thay đổi

```
VIẾT MỚI:
  hush-icore/hush/core/ops/graph/task_scheduler.py   ← WorkflowScheduler class

SỬA:
  hush-icore/hush/core/ops/graph/graph_op.py         ← import + dùng scheduler mới
  hush-icore/hush/core/ops/graph/__init__.py          ← export
  hush-icore/hush/core/engine.py                      ← tạo WorkflowScheduler
  hush-icore/hush/core/tracing/collector.py           ← import _is_gen location mới

XÓA (sau khi tất cả tests pass):
  hush-icore/hush/core/ops/graph/scheduler.py         ← old event-based scheduler
  hush-icore/hush/core/ops/graph/_loop.py             ← old loop wrapper

KHÔNG THAY ĐỔI:
  Tất cả ops (BaseOp, FuncOp, LLMOp, PromptOp, TritonOp, ...)
  StateSchema, MemoryState
  _decorators.py, validation.py
  Tests (KHÔNG sửa test — nếu test fail → scheduler mới sai)
```

---

## Pending: callbot-engine-hush speech pipeline (blocked by this refactor)

Trước khi refactor scheduler, speech pipeline đang bị deadlock ở chained streaming:
```
wav_source (yield) → AudioProcessor (yield) → VadDetector (yield) → STT → Denoise
```

Sau khi scheduler mới hoạt động, cần test lại:

1. `scripts/test_source_only.py` — source → END (đã pass)
2. `scripts/test_source_audio.py` — source → AudioProcessor → END (đã pass)
3. `scripts/test_source_audio_vad.py` — source → AudioProcessor → VadDetector → END (deadlock → cần pass với scheduler mới)
4. `tests/speech/test_speech_pipeline.py` — full pipeline source → audio → vad → STT → denoise (deadlock → cần pass)
5. `tests/speech/test_speech_pipeline.py` với WAV files — end-to-end verify transcript accuracy

Ngoài ra:
- `tests/agents/test_educa_reminder.py` — LLM workflow (đã pass, verify vẫn pass sau refactor)
- TTS pipeline `speech/tts_synthesizer.py` (đã pass, verify vẫn pass)
- Test audio files generated tại `tests/speech/audio/*.wav` (10 files, sẵn sàng)