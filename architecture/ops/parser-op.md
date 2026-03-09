# ParserOp

## Overview

`ParserOp` trích xuất dữ liệu có cấu trúc từ text (thường là output của LLM). Hỗ trợ JSON, XML, và YAML. Được sử dụng độc lập hoặc như thành phần trong `chain()`.

Location: `hush-core/hush/core/ops/transform/parser_op.py`

## Kiến trúc

```
LLM Output (text)
       │
       ▼
┌──────────────┐
│  ParserOp    │
│              │
│  1. Parse    │ ← parse_json/parse_xml/parse_yaml
│  2. Extract  │ ← chain path traversal
│              │
└──────┬───────┘
       │
       ▼
Structured Data (dict)
```

### Trong chain()

```
PromptOp → LLMOp → ParserOp → Output
                      │
                      └── Chỉ khi có `extract` parameter
```

## ExtractField

Dataclass biểu diễn một field cần trích xuất:

```python
@dataclass
class ExtractField:
    output_key: str          # Tên output variable
    chain_path: List[str]    # Đường dẫn nested: ["company", "user", "address"]
    type_hint: str           # Type annotation (metadata)
```

### Schema String Syntax

```
"path.to.field: type_hint"
```

Ví dụ:

| Schema String | output_key | chain_path | type_hint |
|---------------|-----------|------------|-----------|
| `"category: str"` | `category` | `["category"]` | `str` |
| `"user.name: str"` | `name` | `["user", "name"]` | `str` |
| `"response.data.items: list"` | `items` | `["response", "data", "items"]` | `list` |
| `"score"` | `score` | `["score"]` | `Any` (mặc định) |

Factory method `ExtractField.from_string()` parse chuỗi này:

```python
field = ExtractField.from_string("company.user.address: dict")
# field.output_key = "address"
# field.chain_path = ["company", "user", "address"]
# field.type_hint = "dict"
```

## Parsing Functions

Ba hàm parsing cấp module, O(1) lookup qua `_PARSER_MAP`:

```python
_PARSER_MAP = {
    "json": parse_json,
    "xml": parse_xml,
    "yaml": parse_yaml,
}
```

### parse_json

- Strip backtick code blocks (````json...````)
- Gọi `json.loads()`

### parse_xml

- Strip backtick code blocks
- Hỗ trợ single-root: `<root><a>1</a></root>` → `{"root": {"a": "1"}}`
- Hỗ trợ multi-root: `<a>1</a><b>2</b>` → `{"a": "1", "b": "2"}` (auto-wrap trong `<root>`)
- Recursive `xml_to_dict()` cho nested elements
- Hỗ trợ repeated tags → tự động chuyển thành list

### parse_yaml

- Strip backtick code blocks
- Sử dụng `yaml.safe_load()`

### Backtick Stripping

Tất cả parsers đều xử lý LLM output được wrap trong code blocks:

```
\`\`\`json
{"key": "value"}
\`\`\`
```

Logic: Nếu text bắt đầu bằng ``````, bỏ dòng đầu và dòng cuối.

## Chain Path Traversal

Sau khi parse text thành dict, ParserOp trích xuất từng field theo chain_path:

```python
def _extract_value_by_path(self, data, chain_path):
    current = data
    for key in chain_path:
        if isinstance(current, dict) and key in current:
            current = current[key]
        else:
            return None  # Trả về None nếu path không tồn tại
    return current
```

Ví dụ: Với data `{"response": {"items": [1, 2, 3]}}` và chain_path `["response", "items"]`:

```
Step 1: current = data["response"] = {"items": [1, 2, 3]}
Step 2: current = current["items"] = [1, 2, 3]
Return: [1, 2, 3]
```

## Auto Input/Output

ParserOp tự động tạo input/output schema từ `extract` list:

```python
# Từ extract list
extract = ["category: str", "user.name: str", "score: float"]

# Auto-generated:
inputs = {"text": Param(type=str, required=True)}
outputs = {
    "category": Param(),
    "name": Param(),      # output_key = last element của chain_path
    "score": Param(),
}
```

User có thể override bằng `inputs=` và `outputs=` parameter. Schema tự động merge với user-provided (user values ghi đè).

## Sử dụng

### Độc lập

```python
from hush.core.ops import ParserOp

parser = ParserOp(
    name="extract_info",
    format="json",
    extract=["category: str", "confidence: float"],
    inputs={"text": llm_op["content"]},
)
```

### Trong chain()

```python
from hush.providers import chain

c = chain(
    resource="gpt-4o",
    template={"user": "Classify this text: {text}"},
    extract=["category: str", "confidence: float"],
    parser="xml",                # Format cho parser (mặc định "xml")
    text=PARENT["text"],
)
# Output: c["category"], c["confidence"]
```

### Error Handling

Khi parsing thất bại, raise `ParserError` với context:

```python
try:
    parsed_data = self.backend(text)
except Exception as e:
    raise ParserError(
        message="Failed to parse input text",
        input_text=text,        # Text đầu vào
        format_type=self.format, # "json"/"xml"/"yaml"
        original_error=e,
    )
```

## Metadata

`specific_metadata` trả về format đang sử dụng:

```python
{"format": "xml"}  # hoặc "json", "yaml"
```

## Xem thêm

- [Exception Hierarchy](exception-hierarchy.md) - ParserError chi tiết
- [BaseOp Anatomy](base-op.md) - Param system, input/output normalization
- [Workflow Ops](../providers/workflow-ops.md) - chain() tích hợp với ParserOp
