# Providers

## LLMOp

::: hush.providers.ops.llm.LLMOp
    options:
      members:
        - of

## PromptOp

::: hush.providers.ops.prompt.PromptOp
    options:
      members:
        - of

## EmbeddingOp

::: hush.providers.ops.embedding.EmbeddingOp
    options:
      members:
        - of

## RerankOp

::: hush.providers.ops.rerank.RerankOp
    options:
      members:
        - of

## chat()

Shorthand for PromptOp + LLMOp text generation.

::: hush.providers.ops.chain.chat

## extract()

PromptOp + LLMOp + ParserOp with retry and validation.

::: hush.providers.ops.chain.extract
