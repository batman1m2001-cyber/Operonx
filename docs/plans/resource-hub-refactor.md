# ResourceHub Refactor Plan

## Mục tiêu

1. YAML nested format: `llm.gpt-4o` thay vì `llm:gpt-4o` flat
2. `hub.get("llm:gpt-4o")` thay vì `hub.llm("gpt-4o")`
3. Thêm `triton` category
4. `resource=` nhận string (lookup) hoặc dict (inline config)
5. Ops tự infer category: `LLMOp(resource="gpt-4o")` → lookup `llm:gpt-4o`

## YAML Format

```yaml
# TRƯỚC (flat)
llm:gpt-4o:
  api_type: openai
  base_url: ...
llm:claude-haiku:
  api_type: anthropic
  ...

# SAU (nested)
llm:
  gpt-4o:
    api_type: openai
    base_url: ...
  claude-haiku:
    api_type: anthropic
    ...

triton:
  stt:
    url: ${TRITON_URL:192.168.1.212:8001}
    model: fastconformer_asr
  tts:
    url: ${TTS_TRITON_URL:192.168.1.212:3001}
    model: fastspeech2
    vocoder: hifigan

onnx:
  vad:
    model_path: models/silero_vad.onnx
  denoise:
    model_path: models/denoise.onnx
```

## ResourceHub API

```python
# XÓA hết:
hub.llm()  hub.embedding()  hub.onnx()  hub.reranking()

# CHỈ CÒN:
hub.get("llm:gpt-4o")        → initialized instance (BaseLLM)
hub.get("triton:stt")         → config dict
hub.get("onnx:vad")           → initialized instance (OnnxInference)
hub.get("embedding:bge-m3")   → initialized instance (BaseEmbedding)
```

## Op resource= nhận string hoặc dict

```python
# String → lookup (op tự prefix category)
LLMOp(resource="gpt-4o")           # → hub.get("llm:gpt-4o")
TritonOp(resource="stt")            # → hub.get("triton:stt")
OnnxOp(resource="vad")              # → hub.get("onnx:vad")

# Dict → inline config, no yaml, no hub lookup
LLMOp(resource={"api_type": "openai", "base_url": "...", "api_key": "..."})
TritonOp(resource={"url": "192.168.1.212:8001", "model": "fastconformer_asr"})

# KHÔNG CÒN direct params:
# TritonOp(url="...", model_name="...")  ← XÓA
```

## Files thay đổi

### hush-icore
```
hush/core/registry/resource_hub.py    EDIT — get() method, nested YAML loader
hush/core/registry/__init__.py        EDIT — export get()
```

### hush-providers
```
hush/providers/ops/llm.py             EDIT — resource string/dict, auto-prefix
hush/providers/ops/onnx.py            EDIT — resource string/dict, auto-prefix
hush/providers/ops/embedding.py       EDIT — resource string/dict, auto-prefix
hush/providers/ops/rerank.py          EDIT — resource string/dict, auto-prefix
hush/providers/ops/triton.py          EDIT — resource from hub.get()
hush/providers/llms/config.py         EDIT — thêm TritonConfig nếu cần
```

### callbot-engine-hush
```
resources.yaml                         EDIT — nested format + triton section
pipeline/callbot.py                    EDIT — TritonOp dùng resource="stt"
```

### Tests
```
hush-providers/tests/*                 EDIT — update resources fixture
examples/resources.yaml                EDIT — nested format
```

## Implementation order

1. ResourceHub: `get()` method + nested YAML parser, xóa `.llm()` `.embedding()` `.onnx()` `.reranking()`
2. Thêm triton category
3. Ops: resource string/dict, xóa direct params (url, model_name...)
4. Update tất cả resources.yaml (examples, tests, callbot)
5. Sửa tất cả callers (ops, tests, examples)
6. Run all tests
7. Update callbot-engine-hush
