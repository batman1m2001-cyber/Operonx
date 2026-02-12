# Exception Hierarchy

## Overview

Hush sử dụng một hệ thống exception thống nhất cho tất cả các op errors. Mỗi loại op có exception riêng với context đầy đủ để debug, kế thừa từ base class `OpError`.

Location: `hush-core/hush/core/exceptions.py`

## Thiết kế

### Nguyên tắc

1. **Domain-specific**: Mỗi loại op có exception riêng (không dùng generic Exception)
2. **Context-rich**: Tự động thu thập metadata để debug (input, condition, format, ...)
3. **Truncation**: Dữ liệu lớn được tự động cắt ngắn để tránh error message quá dài
4. **Exception chaining**: Lưu original exception qua `original_error`

### Cây kế thừa

```
Exception
  └── OpError                    # Base cho tất cả op errors
        ├── ParserError          # ParserOp: parsing thất bại (JSON/XML/YAML)
        ├── CodeError            # FuncOp: user function thất bại
        ├── BranchError          # BranchOp: đánh giá điều kiện thất bại
        ├── ConditionError       # WhileOp: until thất bại
        ├── IterationError       # ForOp/MapOp: iteration thất bại
        ├── PromptError          # PromptOp: template formatting thất bại
        ├── EmbeddingError       # EmbeddingOp: embedding provider thất bại
        └── RerankError          # RerankOp: reranking provider thất bại
```

## OpError (Base Class)

```python
class OpError(Exception):
    def __init__(self, message, op_type, original_error=None, context=None):
        self.op_type = op_type          # "parser", "code", "branch", ...
        self.original_error = original_error  # Exception gốc
        self.context = context or {}    # Dict metadata tùy theo loại op
```

### Định dạng message

```
[OP_TYPE] message
  Error: original_error (nếu có)
  key1: value1
  key2: value2
```

Ví dụ thực tế:

```
[PARSER] Failed to parse input text
  Error: Expecting ',' delimiter: line 3 column 1 (char 45)
  format: json
  input: '{"name": "Alice", "age": 30...'
```

## Truncation

Hàm helper `_truncate()` cắt ngắn chuỗi dài:

```python
_truncate(text, max_length=200)  # Mặc định 200 ký tự
```

Các giới hạn truncation theo loại dữ liệu:

| Dữ liệu | Max length | Áp dụng cho |
|---------|-----------|-------------|
| Input text | 200 | ParserError input, string values trong context |
| Source code | 300 | CodeError source |
| Input values | 100 | CodeError, BranchError, ConditionError inputs |
| Query | 100 | RerankError query |
| Template | 300 | PromptError template |

## Chi tiết từng Exception

### ParserError

**Khi nào**: ParserOp không parse được text (JSON/XML/YAML không hợp lệ)

```python
raise ParserError(
    message="Failed to parse input text",
    input_text=raw_text,       # Text đầu vào
    format_type="json",        # Loại format
    original_error=json_error  # Exception gốc
)
```

**Context**: `format`, `input`

### CodeError

**Khi nào**: FuncOp chạy user function bị lỗi

```python
raise CodeError(
    message="Function raised an exception",
    function_name="calculate_total",
    source="def calculate_total(x, y): ...",  # Truncated 300
    inputs={"x": 1, "y": 0},                  # Truncated 100/value
    original_error=division_error
)
```

**Context**: `function_name`, `source` (truncated 300), `inputs` (mỗi value truncated 100)

### BranchError

**Khi nào**: BranchOp không đánh giá được điều kiện

```python
raise BranchError(
    message="Condition evaluation failed",
    condition="score >= 90",
    inputs={"score": "invalid"},
    candidates=["excellent", "good", "fail"],
    original_error=type_error
)
```

**Context**: `condition`, `inputs` (truncated 100/value), `candidates`

### ConditionError

**Khi nào**: WhileOp until thất bại (lúc compile hoặc eval)

```python
raise ConditionError(
    message="Stop condition evaluation failed",
    condition="counter >= 5",
    inputs={"counter": 3},
    iteration=3,              # Số iteration hiện tại
    phase="eval",             # "compile" hoặc "eval"
    original_error=name_error
)
```

**Context**: `condition`, `phase`, `inputs` (optional), `iteration` (optional)

### IterationError

**Khi nào**: ForOp/MapOp gặp lỗi tại một iteration cụ thể

```python
raise IterationError(
    message="Iteration 2 failed",
    iteration_index=2,
    loop_data={"item": {"id": 3}},
    total_iterations=10,
    op_type="for",             # "for" hoặc "map"
    original_error=key_error
)
```

**Context**: `iteration_index` (format "2/10"), `loop_data` (truncated 100/value)

### PromptError

**Khi nào**: PromptOp không format được template (thiếu biến)

```python
raise PromptError(
    message="Missing template variable(s)",
    template="Hello {name}, your order {order_id}",
    missing_vars=["order_id"],
    original_error=key_error
)
```

**Context**: `template_type`, `template` (truncated 300), `missing_vars` (optional)

### EmbeddingError

**Khi nào**: EmbeddingOp gặp lỗi từ provider

```python
raise EmbeddingError(
    message="Embedding backend failed",
    resource="bge-m3",
    text_count=100,
    original_error=connection_error
)
```

**Context**: `resource`, `text_count`

### RerankError

**Khi nào**: RerankOp gặp lỗi từ provider

```python
raise RerankError(
    message="Invalid document type",
    resource="bge-m3",
    query="search query",
    document_count=50,
    original_error=type_error
)
```

**Context**: `resource`, `query` (truncated 100), `document_count`

## Op-to-Exception Mapping

| Op | Exception | op_type |
|----|-----------|---------|
| ParserOp | ParserError | `"parser"` |
| FuncOp | CodeError | `"code"` |
| BranchOp | BranchError | `"branch"` |
| WhileOp | ConditionError | `"while"` |
| ForOp | IterationError | `"for"` |
| MapOp | IterationError | `"map"` |
| PromptOp | PromptError | `"prompt"` |
| EmbeddingOp | EmbeddingError | `"embedding"` |
| RerankOp | RerankError | `"rerank"` |

## Thêm Custom Exception

```python
from hush.core.exceptions import OpError, _truncate

class MyCustomError(OpError):
    def __init__(self, message, my_param, original_error=None):
        self.my_param = my_param
        super().__init__(
            message=message,
            op_type="my_op",
            original_error=original_error,
            context={
                "my_param": _truncate(repr(my_param), 200),
            },
        )
```

## Xem thêm

- [BaseOp Anatomy](base-op.md) - Op lifecycle và error handling
- [ParserOp](parser-op.md) - ParserError chi tiết
- [Iteration Ops](iteration-ops.md) - IterationError, ConditionError
- [Branch Op](branch-op.md) - BranchError
