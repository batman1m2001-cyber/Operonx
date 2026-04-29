# operonx.providers

LLM, embedding, reranker, and ONNX provider ops. Provider backends are loaded
lazily — installing `operonx[anthropic]` does not require numpy / onnxruntime
/ torch unless you import their respective modules.

## Provider ops

::: operonx.providers.ops.LLMOp
::: operonx.providers.ops.EmbeddingOp
::: operonx.providers.ops.RerankOp
::: operonx.providers.ops.PromptOp

## High-level helpers

::: operonx.providers.ops.chat
::: operonx.providers.ops.ask
