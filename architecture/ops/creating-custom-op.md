# Creating Custom Ops

## Overview

Hướng dẫn tạo custom op mới trong Hush.

## Cách đơn giản: FuncOp + @op

Đa số trường hợp, bạn chỉ cần wrap function với `@op`:

```python
from hush.core.ops.transform import op

@op
def process_data(text: str, max_length: int = 100) -> dict:
    """Process text data."""
    result = text[:max_length].upper()
    return {"processed": result}

# Sử dụng
my_op = process_data(
    inputs={"text": PARENT["input"], "max_length": 50}
)
```

### Automatic Features

`@op` tự động:
- Parse input schema từ function signature
- Parse output schema từ return dict
- Wrap sync function thành async
- Set description từ docstring

### Return Dict Format

```python
@op
def my_func(x: int) -> dict:
    return {
        "result": x * 2,            # Output variable
        "$tags": ["processed"]       # Special: dynamic tags
    }
```

---

## Custom Op Class

Khi cần control nhiều hơn, tạo class kế thừa từ BaseOp:

### Basic Structure

```python
from typing import Dict, Any, Optional
from hush.core.ops.base import BaseOp
from hush.core.configs.op_config import OpType
from hush.core.utils.common import Param

class MyCustomOp(BaseOp):
    """Custom op description."""

    # 1. Define op type
    type: OpType = "custom"  # hoặc "code", "llm", etc.

    # 2. Additional slots nếu cần
    __slots__ = ['my_attribute']

    def __init__(
        self,
        my_param: str,
        inputs: Dict[str, Any] = None,
        outputs: Dict[str, Any] = None,
        **kwargs
    ):
        # 3. Parse expected inputs/outputs
        parsed_inputs = self._parse_inputs(my_param)
        parsed_outputs = self._parse_outputs()

        # 4. Call super().__init__
        super().__init__(**kwargs)

        # 5. Merge parsed với user-provided
        self.inputs = self._merge_params(parsed_inputs, inputs)
        self.outputs = self._merge_params(parsed_outputs, outputs)

        # 6. Store custom attributes
        self.my_attribute = my_param

        # 7. Set core function
        self.core = self._create_core()

    def _parse_inputs(self, my_param: str) -> Dict[str, Param]:
        """Define expected inputs."""
        return {
            "data": Param(type=str, required=True, description="Input data"),
            "option": Param(type=bool, default=False)
        }

    def _parse_outputs(self) -> Dict[str, Param]:
        """Define expected outputs."""
        return {
            "result": Param(type=str, required=True)
        }

    def _create_core(self):
        """Create core execution function."""
        async def core(data: str, option: bool = False) -> Dict[str, Any]:
            # Your logic here
            result = data.upper() if option else data.lower()
            return {"result": result}
        return core

    def specific_metadata(self) -> Dict[str, Any]:
        """Return op-specific metadata."""
        return {"my_attribute": self.my_attribute}
```

---

## Key Methods to Override

### 1. `__init__`

```python
def __init__(self, **kwargs):
    # Parse inputs/outputs trước super().__init__
    parsed_inputs = {...}
    parsed_outputs = {...}

    super().__init__(**kwargs)

    # Merge với user-provided
    self.inputs = self._merge_params(parsed_inputs, self._normalize_params(inputs))
    self.outputs = self._merge_params(parsed_outputs, self._normalize_params(outputs))

    # Set core function
    self.core = self._my_core_function
```

### 2. `core` Function

Core function nhận inputs và trả về outputs dict:

```python
# Sync
def my_core(x: int, y: int) -> Dict[str, Any]:
    return {"sum": x + y}

# Async
async def my_core(url: str) -> Dict[str, Any]:
    result = await fetch(url)
    return {"content": result}

self.core = my_core
```

### 3. `specific_metadata()`

```python
def specific_metadata(self) -> Dict[str, Any]:
    """Return metadata cho tracing/debugging."""
    return {
        "custom_field": self.custom_field,
        "config": self.config
    }
```

### 4. `run()` (Optional)

Override nếu cần custom execution flow:

```python
async def run(
    self,
    state: 'MemoryState',
    context_id: Optional[str] = None,
    parent_context: Optional[str] = None
) -> Dict[str, Any]:
    # Custom pre-processing
    self._prepare()

    # Call parent run
    result = await super().run(state, context_id, parent_context)

    # Custom post-processing
    self._cleanup()

    return result
```

---

## Container Op (GraphOp-based)

Nếu op chứa graph, kế thừa từ GraphOp:

```python
from hush.core.ops.graph import GraphOp

class MyContainerOp(GraphOp):
    type: OpType = "container"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    def _post_build(self):
        """Hook sau khi build() hoàn thành."""
        # Setup inputs/outputs từ child ops
        self._setup_my_schema()

    async def run(self, state, context_id=None, parent_context=None):
        # Custom container execution
        # Có thể gọi self._run_graph() cho child ops
        pass
```

---

## Provider Op (LLM, Embedding, etc.)

Cho AI/ML ops, pattern thường là:

```python
class LLMOp(BaseOp):
    type: OpType = "llm"

    __slots__ = ['provider', 'model', 'temperature']

    def __init__(
        self,
        provider: str = "openai",
        model: str = "gpt-4",
        temperature: float = 0.7,
        **kwargs
    ):
        parsed_inputs = {
            "messages": Param(type=list, required=True),
            "temperature": Param(type=float, default=temperature),
        }
        parsed_outputs = {
            "content": Param(type=str, required=True),
            "usage": Param(type=dict),
        }

        super().__init__(**kwargs)

        self.inputs = self._merge_params(parsed_inputs, kwargs.get('inputs'))
        self.outputs = self._merge_params(parsed_outputs, kwargs.get('outputs'))

        self.provider = provider
        self.model = model
        self.temperature = temperature
        self.contain_generation = True  # Flag cho tracing

        self.core = self._create_llm_core()

    def _create_llm_core(self):
        async def core(messages, temperature=None):
            # Get LLM client from ResourceHub
            from hush.core.registry import ResourceHub
            client = ResourceHub.instance().get(f"llm:{self.provider}")

            # Call LLM
            response = await client.chat(
                model=self.model,
                messages=messages,
                temperature=temperature or self.temperature
            )

            return {
                "content": response.content,
                "usage": response.usage
            }
        return core
```

---

## Testing Your Op

```python
# Direct call (không cần workflow)
my_op = MyCustomOp(name="test", my_param="value")
result = my_op(data="hello", option=True)
print(result)  # {"result": "HELLO"}

# Trong workflow
with GraphOp(name="test_workflow") as g:
    my_op = MyCustomOp(
        name="processor",
        my_param="config",
        inputs={"data": PARENT["input"]}
    )
    START >> my_op >> END

g.build()
result = await engine.run(g, {"input": "test"})
```

---

## Factory Function với Auto-Naming

Nếu bạn tạo factory function (wrapper) để tạo op, hãy dùng `register_skip()` để auto-naming hoạt động:

```python
from hush.core.utils.auto_name import register_skip
from hush.core.ops.base import split_shorthand_kwargs

def my_op_factory(**kwargs):
    """Factory tạo MyOp với shorthand syntax."""
    input_mappings, init_kwargs = split_shorthand_kwargs(kwargs)
    return MyOp(inputs=input_mappings or None, **init_kwargs)

register_skip(my_op_factory)  # auto-naming skip qua factory frame

# Giờ auto-naming hoạt động:
my_op = my_op_factory(x=PARENT["input"])  # my_op.name == "my_op"
```

Nếu factory là decorator (như `@op`, `@graph`), register wrapper bên trong:

```python
def my_decorator(fn):
    @wraps(fn)
    def wrapper(**kwargs):
        return MyOp(code_fn=fn, **kwargs)

    register_skip(wrapper)  # register AFTER wrapper is defined
    wrapper.__wrapped__ = fn
    return wrapper
```

Chi tiết về auto-naming: [auto-naming.md](auto-naming.md)

---

## Checklist

- [ ] Define `type: OpType`
- [ ] Parse inputs/outputs trong `__init__`
- [ ] Gọi `super().__init__(**kwargs)`
- [ ] Merge parsed với user-provided params
- [ ] Set `self.core` function
- [ ] Implement `specific_metadata()` nếu cần
- [ ] Set `contain_generation = True` nếu có LLM calls
- [ ] `register_skip()` nếu tạo factory function / decorator
- [ ] Test với direct call và trong workflow
