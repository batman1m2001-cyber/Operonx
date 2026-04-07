# Plan: @graph và @op cho class + method

## Motivation

Streaming stateful ops (AudioProcessor, VadDetector) cần:
- **State persist** giữa các lần gọi (resampler, buffer, LSTM state)
- **Internal ops visible** cho tracing/debug
- **Clean code** — không gom hết logic vào 1 method monolithic

Hiện tại phải chọn:
- `BaseOp` class: state OK, nhưng 1 method `_process()` monolithic → không trace được bước bên trong
- `@graph` function: trace OK, nhưng state reset mỗi call (mặc dù cùng instance)
- `GraphOp` class + `with self`: khó wire — child ops không thấy `self`

## Đề xuất: @graph class + @op methods

```python
@graph
class AudioProcessor:
    def __init__(self):
        self.resampler = soxr.ResampleStream(8000, 16000, ...)
        self.buffer = deque()
        self.buffer_len = 0
        # ...conformer state

    def build(self):
        """Define internal graph. Called once after __init__."""
        dec = self.decode(raw_chunk=PARENT["raw_chunk"])
        rb = self.resample_and_buffer(samples=dec["samples"], cmc_time=PARENT["cmc_time"])
        START >> dec >> rb >> END

    @op
    def decode(self, raw_chunk: bytes) -> dict:
        """Stateless — pure function."""
        samples = np.frombuffer(raw_chunk, dtype=np.int16).astype(np.float32) / 32768.0
        return {"samples": samples}

    @op
    async def resample_and_buffer(self, samples: np.ndarray, cmc_time: int):
        """Stateful — uses self.resampler, self.buffer."""
        samples = self.resampler.resample_chunk(samples)
        self.buffer.append(samples)
        self.buffer_len += len(samples)
        while self.buffer_len >= 512:
            chunk = self._extract_chunk()
            yield {"audio": chunk, "cmc_time": cmc_time, "recv_time": now()}
```

**Key points:**
- `@graph` trên class → class becomes GraphOp subclass
- `@op` trên methods → methods become child ops trong graph
- `self` accessible trong methods → state persist tự nhiên
- `build()` define wiring giống `with GraphOp() as g:` block
- Instance tạo 1 lần, `run()` nhiều lần → state persist

## Cách sử dụng

```python
# Trong pipeline
@graph
def callbot_pipeline(wav_path):
    source = wav_source(wav_path=wav_path)
    audio = AudioProcessor(
        inputs={"raw_chunk": source["raw_chunk"], "cmc_time": source["cmc_time"]}
    )
    vad = VadDetector(
        inputs={"audio": audio["audio"], ...}
    )
    START >> source >> audio >> vad >> END
```

Giống hệt cách dùng hiện tại — chỉ internal code clear hơn.

## Cách hoạt động

### `@graph` class decorator

```python
def graph_class(cls):
    """Decorator: convert class thành GraphOp subclass."""
    # 1. cls phải có build() method
    # 2. Inject GraphOp as base class
    # 3. Wrap __init__: gọi super().__init__() + user __init__() + build()
    # 4. Scan methods decorated with @op → register as child ops

    original_init = cls.__init__
    original_build = cls.build

    class GraphClass(GraphOp):
        def __init__(self, **kwargs):
            # GraphOp init (sets up _ops, edges, etc)
            graph_kwargs = {k: v for k, v in kwargs.items() if k in ('name', 'inputs', 'outputs')}
            super().__init__(**graph_kwargs)

            # User init (sets up state: resampler, buffer, etc)
            original_init(self, **{k: v for k, v in kwargs.items() if k not in graph_kwargs})

            # Build graph (define ops + wiring)
            with self:
                original_build(self)

        # Copy all @op methods from original class
        # They become FuncOp children with self bound

    return GraphClass
```

### `@op` method decorator

```python
def op_method(fn):
    """Mark method as child op in @graph class."""
    fn._is_graph_op = True
    return fn
```

Khi `build()` gọi `self.decode(...)`, decorator:
1. Tạo FuncOp wrapping `fn` với `self` bound
2. Register vào `self._ops`
3. Return Ref (giống `@op` function hiện tại)

## Implementation cần sửa trong Hush core

### 1. `_decorators.py` — thêm `graph` cho class

Hiện tại `@graph` chỉ nhận function. Cần detect: nếu argument là class → dùng `_graph_class()` decorator. Nếu function → dùng `_graph_fn()` hiện tại.

```python
def graph(fn_or_cls):
    if isinstance(fn_or_cls, type):
        return _graph_class(fn_or_cls)
    else:
        return _graph_fn(fn_or_cls)
```

### 2. `_decorators.py` — thêm `_graph_class()`

```python
def _graph_class(cls):
    """Convert class → GraphOp subclass with @op methods as child ops."""

    # Collect @op methods
    op_methods = {}
    for name, method in inspect.getmembers(cls, predicate=inspect.isfunction):
        if getattr(method, '_is_graph_op', False):
            op_methods[name] = method

    # Build new class extending GraphOp
    ...
```

### 3. `@op` — extend cho method decorator

Hiện tại `@op` tạo FuncOp instance ngay. Cho method, cần **defer** — chỉ mark, tạo FuncOp khi `build()` chạy.

```python
def op(fn):
    if _is_inside_class():
        # Method: just mark, defer FuncOp creation
        fn._is_graph_op = True
        return fn
    else:
        # Function: create FuncOp immediately (hiện tại)
        return FuncOp(...)
```

Vấn đề: không detect được `_is_inside_class()` lúc decorator chạy (Python limitation). Giải pháp: `@op` trên method luôn mark, `build()` tạo FuncOp.

Hoặc: dùng decorator riêng — `@graph.op` cho methods, `@op` cho functions:

```python
@graph
class AudioProcessor:
    @graph.op
    def decode(self, raw_chunk):
        ...
```

### 4. `graph_op.py` — GraphOp chấp nhận child ops từ methods

Khi `build()` gọi `self.decode(...)`:
- `self.decode` là method decorated → khi gọi, tạo FuncOp bound tới `self`
- FuncOp register vào `self._ops`
- Return Ref cho wiring

## Thay đổi files

```
VIẾT MỚI / SỬA:
  hush-icore/hush/core/ops/graph/_decorators.py  — _graph_class(), graph cho class
  hush-icore/hush/core/ops/graph/graph_op.py     — support method-based child ops

KHÔNG THAY ĐỔI:
  BaseOp, FuncOp, StateSchema, MemoryState, scheduler
  Tất cả ops hiện tại (@op functions, BaseOp classes)
  Tests hiện tại

THÊM:
  hush-icore/tests/ops/test_graph_class.py — tests cho @graph class
```

## Ví dụ VadDetector (sau refactor)

```python
@graph
class VadDetector:
    def __init__(self, model_path="models/silero_vad.onnx"):
        self._session = _get_vad_session(model_path)
        self._vad_state = np.zeros((2, 1, 128), dtype=np.float32)
        self._vad_context = np.zeros((1, 64), dtype=np.float32)
        self._triggered = False
        self._speech_buffer = deque()
        self._bg_model = OnlineGaussianModel()
        # ...

    def build(self):
        infer = self.vad_inference(audio=PARENT["audio"])
        fg = self.foreground_detect(audio=PARENT["audio"], prob=infer["prob"])
        seg = self.speech_segmenter(
            prob=infer["prob"],
            is_foreground=fg["is_foreground"],
            audio=PARENT["audio"],
            cmc_time=PARENT["cmc_time"],
        )
        START >> infer >> fg >> seg >> END

    @graph.op
    def vad_inference(self, audio: np.ndarray) -> dict:
        input_ctx = np.concatenate([self._vad_context, audio.reshape(1, -1)], axis=1)
        prob, new_state = self._session.run(...)
        self._vad_state = new_state
        self._vad_context = input_ctx[:, -64:]
        return {"prob": prob}

    @graph.op
    def foreground_detect(self, audio: np.ndarray, prob: float) -> dict:
        energy = np.sqrt(np.mean(audio ** 2))
        self._bg_model.update(energy)
        is_fg = self._bg_model.p_background(energy) < 0.2
        return {"is_foreground": is_fg}

    @graph.op
    async def speech_segmenter(self, prob, is_foreground, audio, cmc_time):
        # State machine logic...
        if speech_end_detected:
            yield {"speech_audio": segment, "duration_ms": dur, ...}
```

## Test Strategy

1. Test `@graph class` tạo GraphOp instance đúng
2. Test `@graph.op` methods register as child ops
3. Test state persist across calls (resampler, buffer)
4. Test streaming (yield trong methods)
5. Test nesting trong pipeline graph
6. Run existing 690 hush tests — no regression
