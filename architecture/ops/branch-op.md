# BranchOp - Conditional Routing

## Overview

`BranchOp` đánh giá các điều kiện và định tuyến workflow đến các ops khác nhau.

Location: `hush-core/hush/core/ops/flow/branch_op.py`

## Class Definition

```python
class BranchOp(BaseOp):
    type: OpType = "branch"

    __slots__ = [
        'given_candidates',  # List explicit candidates
        'default',          # Default target name
        'cases',            # List[(Ref, target_name)]
    ]
```

## Hai cách tạo BranchOp

### 1. Trực tiếp với Ref conditions

```python
branch = BranchOp(
    name="router",
    cases=[
        (PARENT["score"] >= 90, "excellent"),
        (PARENT["score"] >= 70, "good"),
        (PARENT["score"] >= 50, "average"),
    ],
    default="fail",
)
```

### 2. Fluent Builder (Branch class)

```python
branch = (Branch("router")
    .if_(PARENT["score"] >= 90, excellent_op)
    .if_(PARENT["score"] >= 70, good_op)
    .if_(PARENT["score"] >= 50, average_op)
    .else_(fail_op))
```

## Input Parsing

BranchOp tự động parse inputs từ điều kiện:

```python
def _parse_cases(self, cases) -> tuple:
    # Luôn có anchor input để override routing
    inputs = {"anchor": Param(type=str, default=None)}

    # Parse biến từ Ref conditions
    for ref, target in cases:
        inputs[ref.var] = Param(required=True)

    outputs = {
        "target": Param(type=str, required=True),
        "matched": Param(type=str)
    }

    return inputs, outputs
```

## Execution

### Core Function

```python
def _create_core_function(self):
    def core(**inputs) -> Dict[str, str]:
        # 1. Check anchor override trước
        anchor = inputs.get('anchor')
        if anchor:
            return {"target": anchor, "matched": "anchor"}

        # 2. Evaluate conditions
        target, matched = self._evaluate_conditions(inputs)
        return {"target": target, "matched": matched}

    return core
```

### Condition Evaluation

```python
def _evaluate_conditions(self, inputs) -> tuple:
    safe_inputs = dict(inputs)

    for ref, target in self.cases:
        try:
            value = safe_inputs.get(ref.var)
            result = ref.execute(value)  # Execute all ops
            if result:
                return target, f"ref:{ref.var}"
        except Exception:
            continue

    # Default target
    if self.default:
        return self.default, "default"

    return None, None
```

## Anchor Override

Anchor cho phép override routing dynamically:

```python
# Trong workflow
branch = BranchOp(
    name="router",
    cases=[(PARENT["score"] >= 90, "excellent")],
    default="average",
    inputs={
        "anchor": PARENT.get("force_route", None)  # Override nếu có
    }
)

# Runtime: nếu force_route = "excellent", sẽ route đến excellent
# bất kể score là bao nhiêu
```

## Graph Integration

### Soft Edges với Branch

Branch outputs thường dùng soft edges vì chỉ 1 nhánh được thực thi:

```python
with GraphOp(name="workflow") as g:
    branch = BranchOp(
        name="router",
        cases=[(PARENT["score"] >= 70, "pass")],
        default="fail"
    )

    pass_handler = FuncOp(name="pass", ...)
    fail_handler = FuncOp(name="fail", ...)
    merge = FuncOp(name="merge", ...)

    START >> branch
    branch >> ~pass_handler >> merge   # Soft edge
    branch >> ~fail_handler >> merge   # Soft edge
    merge >> END
```

### GraphOp Execution

GraphOp xử lý branch đặc biệt:

```python
# Trong GraphOp.run()
if op.type == "branch":
    branch_target = op.get_target(state, context_id)
    if branch_target != END.name:
        next_ops = [branch_target]  # Chỉ 1 target
    else:
        next_ops = []
else:
    next_ops = self.nexts[op_name]  # Tất cả successors
```

## Fluent Builder

### Branch Class

```python
class Branch:
    """Fluent builder để tạo BranchOp."""

    def __init__(self, name: str, **kwargs):
        self._name = name
        self._cases: List[Tuple[Ref, str]] = []
        self._default: Optional[str] = None
        self._kwargs = kwargs

    def if_(self, condition: Ref, target) -> 'Branch':
        """Thêm case với Ref condition."""
        target_name = target.name if hasattr(target, 'name') else target
        self._cases.append((condition, target_name))
        return self

    def else_(self, target) -> 'BranchOp':
        """Set default và build op."""
        self._default = target.name if hasattr(target, 'name') else target
        return self._build()
```

### Ref Conditions

Ref hỗ trợ comparison operators:

```python
# Tạo Ref với comparison
PARENT["score"] >= 90    # Ref với op: ('>=', 90)
PARENT["status"] == "ok" # Ref với op: ('==', "ok")
PARENT["items"].apply(len) > 0  # Ref với apply() và comparison

# Fluent API sử dụng
(Branch("router")
    .if_(PARENT["score"] >= 90, "excellent")
    .if_(PARENT["items"].apply(len) > 0, "has_items")
    .else_("default"))
```

## Metadata

```python
def specific_metadata(self) -> Dict[str, Any]:
    return {
        "cases": [(str(ref), target) for ref, target in self.cases],
        "default_target": self.default,
        "candidates": self.candidates,
        "num_conditions": len(self.cases)
    }
```

## Candidates

```python
@property
def candidates(self) -> List[str]:
    """Danh sách tất cả possible targets."""
    if self.given_candidates:
        return self.given_candidates

    targets = [target for _, target in self.cases]

    if self.default:
        targets.append(self.default)

    return targets
```
