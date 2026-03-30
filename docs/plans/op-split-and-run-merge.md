# Plan: Op Splitting + Run/Generator Merge

## Part 1: Op Splitting

### Phân tích dependency

```
                          AUDIO PROCESSOR
                          ═══════════════
decode_pcm (stateless)
    ↓
resample (stateful: resampler phase)
    ↓
buffer_extract (stateful: deque accumulator, N-to-M yield)
    ↓
bandpass_filter (stateless: coefficients immutable)
    ↓
pre_emphasis (stateless)
    ↓
speech_detect (stateful: energy_buffer, speech_frames, silence_frames)
    ├──→ noise_update (if silence, stateful: noise_samples)
    │        ↓
    │    spectral_gating (reads noise_samples, otherwise stateless per-chunk)
    ↓        ↓
adaptive_gain (stateful: rms_buffer, current_gain) ← needs is_speech + gated audio


                          VAD DETECTOR
                          ════════════
         ┌─── vad_infer (stateful: LSTM, 1-to-1)
audio ───┤                                          → speech_segmenter (stateful: state machine, N-to-M)
         └─── foreground_detect (stateful: EWMA, 1-to-1)
              (PARALLEL với vad_infer ✓)
```

### AudioProcessor: tách thành 7 ops

```python
@graph
def audio_processor(raw_chunk, cmc_time):
    PARENT.shared(
        resamp={"resampler": soxr.ResampleStream(...)},  # mutable dict
        buf={"buffer": deque(), "len": 0},                # mutable dict
        detect={"energy_buffer": deque(maxlen=50), "speech_frames": 0, "silence_frames": 0},
        noise={"samples": []},
        gain={"rms_buffer": deque(maxlen=50), "current_gain": 1.0},
    )

    # 1. Decode PCM (stateless)
    dec = decode_pcm(raw_chunk=raw_chunk)

    # 2. Resample (stateful, phase)
    res = resample_chunk(samples=dec["samples"], resamp=PARENT["resamp"])

    # 3. Buffer extract (stateful, N-to-M yield)
    chunk = buffer_extract(resampled=res["resampled"], buf=PARENT["buf"])

    # 4. Bandpass filter (stateless — coefficients are module constants)
    bp = bandpass_filter(audio=chunk["audio"])

    # 5. Pre-emphasis (stateless)
    pe = pre_emphasis(audio=bp["audio"])

    # 6. Speech detect + noise update + spectral gating
    #    (sequential: detect → noise → gating, all stateful)
    sd = speech_detect(audio=pe["audio"], detect=PARENT["detect"])
    nu = noise_update(audio=pe["audio"], is_speech=sd["is_speech"], noise=PARENT["noise"])
    sg = spectral_gating(audio=pe["audio"], noise=PARENT["noise"])

    # 7. Adaptive gain (stateful)
    ag = adaptive_gain(audio=sg["audio"], is_speech=sd["is_speech"], gain=PARENT["gain"])

    # Emit with timestamps
    emit = emit_chunk(audio=ag["audio"], cmc_time=cmc_time, recv_time=chunk["recv_time"])

    START >> dec >> res >> chunk >> bp >> pe >> sd >> nu >> sg >> ag >> emit >> END
```

**Parallelism trong AudioProcessor:** Không — pipeline sequential vì mỗi step cần output step trước. Nhưng **tracing/debug** tốt hơn — nhìn thấy timing mỗi step riêng.

### VadDetector: giữ 3 ops (đã OK)

```python
@graph
def vad_detector(audio, cmc_time, recv_time):
    PARENT.shared(onnx_state={...}, bg={...}, sm={...}, ...)

    infer = vad_infer(audio=audio, onnx_state=PARENT["onnx_state"])
    fg = foreground_detect(audio=audio, bg=PARENT["bg"])
    seg = speech_segmenter(
        audio=audio, cmc_time=cmc_time,
        speech_prob=infer["speech_prob"],
        is_foreground=fg["is_foreground"],
        sm=PARENT["sm"], ...
    )

    START >> [infer, fg]       # PARALLEL
    [infer, fg] >> seg >> END  # seg chờ cả hai
```

**Không cần tách thêm** — segmenter là 1 unit logic (state machine), tách nhỏ hơn chỉ thêm complexity.

### Kiểm tra đồng nhất v1 vs v2

```python
# Script: compare_v1_v2.py
async def compare(wav_path):
    # V1: BaseOp
    proc_v1 = AudioProcessor(name="v1")
    vad_v1 = VadDetector(name="v1_vad")

    # V2: @graph
    # ... build pipeline ...

    # Feed same audio, compare outputs
    chunks_v1 = []
    chunks_v2 = []
    segments_v1 = []
    segments_v2 = []

    for chunk in wav_chunks:
        # V1
        async for out in proc_v1._process(raw_chunk=chunk, cmc_time=0):
            chunks_v1.append(out["audio"])
            async for seg in vad_v1._process(audio=out["audio"], ...):
                segments_v1.append(seg)

        # V2 (via Hush engine)
        ...

    # Compare
    assert len(chunks_v1) == len(chunks_v2)
    for i, (c1, c2) in enumerate(zip(chunks_v1, chunks_v2)):
        np.testing.assert_allclose(c1, c2, atol=1e-6)

    assert len(segments_v1) == len(segments_v2)
    for s1, s2 in zip(segments_v1, segments_v2):
        np.testing.assert_allclose(s1["speech_audio"], s2["speech_audio"], atol=1e-6)
```

---

## Part 2: Merge run() + _run_generator()

### Vấn đề hiện tại

```
Scheduler dispatch op:
    │
    ├── _is_gen(op)? ──YES──→ _run_generator()
    │                           - gọi op.core(**inputs) iterate yields
    │                           - mỗi yield → store_result + yield_queue.put
    │                           - KHÔNG gọi op.run()
    │
    └── NO ──→ task_execute()
                - gọi op.run(state, ctx, parent)
                - run() nội bộ: get_inputs + core + store_result + metrics
                - return result dict
                - scheduler propagate

Vấn đề:
- 2 code paths khác nhau cho cùng 1 mục đích (execute op, store result)
- run() làm get_inputs + core + store_result + metrics
- _run_generator() cũng làm get_inputs + core + store_result + metrics
- Duplicate logic, dễ desync
- GraphOp streaming cần thêm _run_streaming() → thêm code path thứ 3
```

### Thiết kế mới: 1 hàm run() duy nhất

```python
class BaseOp:
    async def run(self, state, context_id, parent_context):
        """Thực thi op. Không return gì. Store result vào state."""
        inputs = self.get_inputs(state, context_id, parent_context)

        if self._is_generator:
            # Generator: yield per-item
            idx = 0
            async for result in self.core(**inputs):
                stream_ctx = context_id + (f"[{idx}]",)
                self.store_result(state, result, stream_ctx)
                # Notify scheduler (qua callback hoặc yield_queue)
                if self._yield_callback:
                    await self._yield_callback(self.name, stream_ctx, result)
                idx += 1
        else:
            # Regular: run once
            result = await self._exec_core(inputs)
            self.store_result(state, result, context_id)

        # Metrics (same cho cả 2 mode)
        self._store_metrics(state, context_id, ...)
```

**Thay đổi:**
- `run()` **không return gì** (void)
- Generator logic **trong run()**, không tách _run_generator()
- Scheduler **không cần biết** op là generator hay không — chỉ gọi `run()`
- `_yield_callback` inject bởi scheduler trước khi gọi run()

### Scheduler đơn giản hóa

```python
# TRƯỚC: 2 paths
async def run_op(task):
    if _is_gen(op):
        await _run_generator(task, op, yield_queue, ...)
        return task, "exhausted"
    else:
        result = await op.run(state, ctx, parent)
        return task, result

# SAU: 1 path
async def run_op(task):
    op._yield_callback = lambda name, ctx, result: yield_queue.put(YieldEvent(...))
    await op.run(state, ctx, parent)
    return task, "done"

# Scheduler xử lý:
# - "done" → propagate (cho non-generator)
# - YieldEvent → _handle_yield (cho generator, nhận qua yield_queue)
# - Cả hai đều fire tự động, scheduler không cần biết op type
```

### GraphOp cũng dùng cùng run()

```python
class GraphOp(BaseOp):
    async def run(self, state, context_id, parent_context):
        """Run inner graph. yield per-item if streaming."""
        inputs = self.get_inputs(state, context_id, parent_context)
        _outputs, stream_ctxs = await run_task_scheduler(self, state, ...)

        if self._has_streaming:
            # Yield per-item (same as generator op)
            for i in range(list_len):
                item = {k: v[i] for k, v in _outputs.items()}
                self.store_result(state, item, context_id + (f"[{i}]",))
                if self._yield_callback:
                    await self._yield_callback(self.name, ctx, item)
        else:
            self.store_result(state, _outputs, context_id)
```

### Files thay đổi

```
EDIT:
  hush-icore/hush/core/ops/base.py         — run() merged, void return
  hush-icore/hush/core/ops/graph/graph_op.py  — run() uses same pattern
  hush-icore/hush/core/ops/graph/task_scheduler.py — remove _run_generator, simplify run_op
  hush-icore/hush/core/ops/graph/scheduler.py — old scheduler (if still used)

KHÔNG THAY ĐỔI:
  Tất cả @op, BaseOp subclasses — chúng chỉ define core(), run() tự xử lý
  StateSchema, MemoryState
  Tests (KHÔNG sửa — nếu fail → implementation sai)
```

### Verification

1. Chạy full test suite sau merge: 692+ tests
2. Chạy callbot pipeline tests
3. Compare v1 vs v2 output cho audio/vad
4. Benchmark latency không regression

---

## Thứ tự thực hiện

1. **Op splitting** (#1) trước — không đụng core Hush
   - Tách audio_processor_v2 thành 7 ops
   - Giữ vad_detector_v2 (đã OK)
   - Viết compare script v1 vs v2
   - Test state isolation
   - Benchmark latency

2. **Run/generator merge** (#2) sau — core Hush change
   - Add `_yield_callback` mechanism
   - Merge generator logic vào run()
   - Remove _run_generator()
   - Update scheduler
   - Full test suite
