# Auto-Naming — Variable Name Extraction

## Overview

Khi tao op ma khong truyen `name=`, Hush tu dong lay ten bien tu assignment statement:

```python
llm = LLMOp.of(resource_key="gpt-4o", messages=msgs)
# llm.name == "llm"
```

Module: `hush-core/hush/core/utils/auto_name.py`

## Public API

| Function | Mo ta |
|----------|-------|
| `auto_name()` | Trich xuat variable name tu caller frame |
| `unique_name()` | Tao 8-char hex UUID fallback |
| `register_skip(fn)` | Dang ky function de skip frame khi walking |

## Chien luoc: Bytecode -> Source -> None

```
auto_name() called from BaseOp.__init__
    |
    v
Walk stack (skip __init__, registered, legacy)
    |
    v
_name_from_bytecode(frame)    <- Primary
    | success? return name
    v fail?
_name_from_source(filename, lineno)  <- Fallback
    | success? return name
    v fail?
return None  -> BaseOp dung unique_name()
```

## Frame Walking

`auto_name()` goi `inspect.currentframe()`, roi walk nguoc call stack:

```python
frame = inspect.currentframe()
frame = frame.f_back  # skip auto_name() itself
while frame and _should_skip(frame):
    frame = frame.f_back
```

### Frames duoc skip

1. **`__init__` methods**: Constructor chain (`BaseOp.__init__` -> `GraphOp.__init__` -> ...)
2. **Registered code objects**: Functions dang ky qua `register_skip()` — bao gom `.of()` classmethods, `@op` wrapper, `@graph` wrapper
3. **Legacy marker**: Frames co `_skip_auto_name = True` (backward compat)

```python
_skip_code_objects: Set[CodeType] = set()

def _should_skip(frame) -> bool:
    if frame.f_code.co_name == "__init__":
        return True
    if frame.f_code in _skip_code_objects:
        return True
    if frame.f_locals.get("_skip_auto_name"):
        return True
    return False
```

## Bytecode Analysis (Primary)

Phan tich bytecode cua caller frame de tim `STORE_FAST`/`STORE_NAME` instruction ngay sau call site.

### Tai sao bytecode?

- Khong can source code (hoat dong trong REPL, `exec()`, no-source environments)
- Xu ly multi-line expressions tu nhien (bytecode da flattened)
- Nhanh hon source parsing

### Cach hoat dong

```python
_STORE_OPS = frozenset({"STORE_NAME", "STORE_FAST", "STORE_DEREF", "STORE_GLOBAL"})
_BENIGN_OPS = frozenset({"DUP_TOP", "NOP", "RESUME", "COPY", "CACHE"})

def _name_from_bytecode(frame) -> Optional[str]:
    instructions = list(dis.get_instructions(frame.f_code))
    offset = frame.f_lasti  # current instruction offset

    # Tim instruction dau tien SAU call site
    i = 0
    while i < len(instructions) and instructions[i].offset <= offset:
        i += 1

    # Scan 4 instructions tiep theo
    for j in range(i, min(i + 4, len(instructions))):
        opname = instructions[j].opname
        if opname in _STORE_OPS:
            return instructions[j].argval  # <- ten bien
        if opname in _BENIGN_OPS:
            continue
        break  # non-trivial instruction -> khong phai simple assignment

    return None
```

### Vi du bytecode

```python
# Source: llm = LLMOp.of(resource_key="gpt-4o")
#
# Bytecode:
#   LOAD_GLOBAL    LLMOp
#   LOAD_ATTR      of
#   LOAD_CONST     'gpt-4o'
#   KW_NAMES       ('resource_key',)
#   CALL           1
#   STORE_FAST     llm        <- auto_name tra ve "llm"
```

## Source Parsing (Fallback)

Khi bytecode that bai, parse source code bang AST:

```python
def _name_from_source(filename: str, lineno: int) -> Optional[str]:
    # Thu len den 6 dong tren (multi-line assignment)
    for offset in range(6):
        line = linecache.getline(filename, lineno - offset)
        name = _parse_assignment(line.strip())
        if name is not None:
            return name
    return None
```

### `_parse_assignment()` — Parse single line

```python
def _parse_assignment(line: str) -> Optional[str]:
    try:
        tree = ast.parse(line)
        stmt = tree.body[0]
        # Simple: name = expr
        if isinstance(stmt, ast.Assign) and isinstance(stmt.targets[0], ast.Name):
            return stmt.targets[0].id
        # Annotated: name: Type = expr
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            return stmt.target.id
    except SyntaxError:
        # Multi-line fallback: "var = (" -> regex
        m = re.match(r"(\w+)\s*=(?!=)", line)
        if m and m.group(1).isidentifier():
            return m.group(1)
    return None
```

### Rejected patterns

- `==`, `>=`, `!=` — comparisons
- `a, b = ...` — tuple unpack
- `obj.attr = ...` — attribute assignment
- `d["key"] = ...` — subscript assignment

## register_skip()

Dang ky code object cua function de frame walking skip qua:

```python
def register_skip(fn):
    _skip_code_objects.add(fn.__code__)
    return fn
```

### Ai dung register_skip?

| Location | Registered function |
|----------|-------------------|
| `@shorthand` decorator | `.of()` classmethods (ForOp.of, MapOp.of, ...) |
| `@op` decorator | wrapper function |
| `@graph` decorator | wrapper function |
| `Branch.else_()` | method |
| `Branch.build()` | method |
| `Branch._build()` | method |

### Tai sao can skip?

```python
# Khong skip: auto_name tim thay "wrapper" ben trong func_op wrapper
@op
def greet(name: str):
    return {"greeting": f"Hello, {name}!"}

g = greet(name="world")  # Muon name == "g"
# Call stack: BaseOp.__init__ -> wrapper -> caller (g = ...)
#                                 ^ skip nay!
```

## Integration voi BaseOp

```python
class BaseOp:
    def __init__(self, name=None, **kwargs):
        if name is None:
            name = auto_name() or unique_name()
        self.name = name
```

## Edge Cases

| Case | Ket qua |
|------|---------|
| `g = greet(name="world")` | `g.name == "g"` (bytecode) |
| `ops = [BaseOp()]` | `name == "ops"` (bytecode sees list store) |
| `BaseOp()` (no assignment) | UUID fallback |
| `a = b = BaseOp()` | Bytecode thay store dau tien |
| REPL / exec | Bytecode van hoat dong |

## Tao custom factory voi auto-naming

Neu ban tao factory function cho op:

```python
from hush.core.utils.auto_name import register_skip

def my_factory(**kwargs):
    return MyOp(**kwargs)

register_skip(my_factory)  # <- auto-naming skip qua factory frame

# Gio auto-naming hoat dong:
my_op = my_factory(x=10)  # my_op.name == "my_op"
```

Xem them: [creating-custom-op.md](creating-custom-op.md) cho chi tiet ve custom op patterns.
