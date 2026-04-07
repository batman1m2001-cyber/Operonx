"""Ref type for zero-copy variable references with chainable transforms."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Set, Tuple, Union

if TYPE_CHECKING:
    from hush.core.ops.base import BaseOp

__all__ = ["Ref"]


@dataclass
class StreamPolicy:
    collect: bool = False
    parallel: bool = False
    parallel_max: int = 0  # 0 means unlimited


class Ref:
    """Reference to another op's variable with chainable transforms.

    Transforms are recorded and compiled into a callable for fast execution.
    Ref enables zero-copy data access from other ops, with support for
    getitem, getattr, arithmetic, comparison, and other transforms.

    Supports compound boolean operations with ``&`` and ``|``::

        (PARENT["x"] > 10) & (PARENT["y"] == "active")
        (node["a"]) | (node["b"])
        ~(node["flag"])  # negation

    Example::

        # Access another op's output
        ref = op["output_var"]

        # Chain transforms
        ref = PARENT["data"]["key"].upper()

        # Output mapping
        op["src"] >> PARENT["dest"]

    Attributes:
        _source: Source node (BaseOp or string name).
        var: Source variable name.
        _transforms: List of recorded transforms.
        _fn: Compiled transform function (signature: ``fn(value, context) -> result``).
        idx: Storage index in schema (set by ``StateSchema._build()``).
        is_output: True if this is an output ref (pushes value outward).
    """

    __slots__ = (
        "_source",
        "var",
        "_transforms",
        "_fn",
        "idx",
        "is_output",
        "_stream_parallel",
        "_stream_parallel_max",
        "_stream_collect",
    )

    _RESERVED_ATTRS = frozenset(
        {
            "_source",
            "var",
            "_transforms",
            "_fn",
            "idx",
            "is_output",
            "source",
            "raw_source",
            "transforms",
            "as_tuple",
            "apply",
            "execute",
            "has_transforms",
            "_with_transform",
            "_clone",
            "_resolve",
            "get_all_vars",
            "parallel",
            "collect",
            "_stream_parallel",
            "_stream_parallel_max",
            "_stream_collect",
        }
    )

    def __init__(
        self,
        node: Union["BaseOp", str],
        var: str,
        _transforms: Optional[List[Tuple[str, Any]]] = None,
        _fn: Optional[Callable] = None,
        is_output: bool = False,
    ) -> None:
        """Initialize a Ref.

        Args:
            node: Source node (BaseOp or string node name).
            var: Source variable name.
            _transforms: Transform list (used for deserialization).
            _fn: Function đã compile (dùng cho clone)
            is_output: True nếu là output ref
        """
        object.__setattr__(self, "_source", node)
        object.__setattr__(self, "var", var)
        object.__setattr__(self, "_transforms", _transforms or [])
        object.__setattr__(self, "idx", -1)  # Được set bởi StateSchema._build()
        object.__setattr__(self, "is_output", is_output)  # True cho output ref
        object.__setattr__(self, "_stream_parallel", False)
        object.__setattr__(self, "_stream_parallel_max", None)
        object.__setattr__(self, "_stream_collect", False)
        # Nếu có transforms nhưng không có fn, rebuild từ transforms (trường hợp deserialization)
        if _fn is None and _transforms:
            _fn = lambda x, ctx={}: x
            for op, args in _transforms:
                _fn = self._wrap(_fn, op, args)
        object.__setattr__(self, "_fn", _fn or (lambda x, ctx={}: x))

    @property
    def source(self) -> str:
        """Tên đầy đủ của node nguồn."""
        return self._source.full_name if hasattr(self._source, "full_name") else self._source

    @property
    def raw_source(self) -> Union["BaseOp", str]:
        """Node nguồn gốc (có thể là object hoặc string)."""
        return self._source

    @property
    def transforms(self) -> List[Tuple[str, Tuple[Any, ...]]]:
        """Danh sách các transform đã ghi."""
        return self._transforms

    @property
    def has_transforms(self) -> bool:
        """Kiểm tra có transform nào không."""
        return len(self._transforms) > 0

    def as_tuple(self) -> Tuple[str, str]:
        """Trả về tuple (op_name, var_name)."""
        return (self.source, self.var)

    def _clone(self) -> "Ref":
        """Tạo bản sao của Ref."""
        new = Ref(self._source, self.var, list(self._transforms), self._fn, self.is_output)
        object.__setattr__(new, "_stream_parallel", self._stream_parallel)
        object.__setattr__(new, "_stream_parallel_max", self._stream_parallel_max)
        object.__setattr__(new, "_stream_collect", self._stream_collect)
        return new

    def parallel(self, max: int = None) -> "Ref":
        """Mark for parallel consumption. Default sequential → parallel.

        Args:
            max: Max concurrent items. None = unlimited.

        Returns:
            New Ref with parallel mode set.

        Example::

            op_b(x=source["x"].parallel())       # unlimited parallel
            op_b(x=source["x"].parallel(max=4))   # max 4 concurrent
        """
        new = self._clone()
        object.__setattr__(new, "_stream_parallel", True)
        object.__setattr__(new, "_stream_parallel_max", max)
        return new

    def collect(self) -> "Ref":
        """Collect all streamed items into a list before dispatching downstream.

        Waits until source generator exhausts, then dispatches downstream
        once with list of all items.

        Returns:
            New Ref with collect mode set.

        Example::

            op_b(items=source["x"].collect())                   # sequential + collect
            op_b(items=source["x"].parallel().collect())        # parallel + collect
            op_b(items=source["x"].parallel(max=4).collect())   # bounded parallel + collect
        """
        new = self._clone()
        object.__setattr__(new, "_stream_collect", True)
        return new

    def _with_transform(self, op: str, *args: Any) -> "Ref":
        """Tạo Ref mới với thêm một transform."""
        new_transforms = self._transforms + [(op, args)]
        new_fn = self._wrap(self._fn, op, args)
        new_ref = Ref(self._source, self.var, new_transforms, new_fn)
        object.__setattr__(new_ref, "_stream_parallel", self._stream_parallel)
        object.__setattr__(new_ref, "_stream_parallel_max", self._stream_parallel_max)
        object.__setattr__(new_ref, "_stream_collect", self._stream_collect)
        return new_ref

    @staticmethod
    def _wrap(fn: Callable, op: str, args: Tuple) -> Callable:
        """Wrap function với thêm một transform.

        All lambdas have signature: fn(value, context={}) -> result
        Context is a dict containing all available variable values,
        used for resolving compound boolean operations.
        Context defaults to {} for backward compatibility.
        """
        a = args[0] if args else None

        match op:
            # Truy cập
            case "getitem":
                return lambda x, ctx={}, f=fn, k=a: f(x, ctx)[k]
            case "getattr":
                return lambda x, ctx={}, f=fn, k=a: getattr(f(x, ctx), k)
            case "call":
                ca, kw = args
                return lambda x, ctx={}, f=fn, a=ca, k=kw: f(x, ctx)(*a, **k)
            # Số học
            case "add":
                return lambda x, ctx={}, f=fn, v=a: f(x, ctx) + v
            case "radd":
                return lambda x, ctx={}, f=fn, v=a: v + f(x, ctx)
            case "sub":
                return lambda x, ctx={}, f=fn, v=a: f(x, ctx) - v
            case "rsub":
                return lambda x, ctx={}, f=fn, v=a: v - f(x, ctx)
            case "mul":
                return lambda x, ctx={}, f=fn, v=a: f(x, ctx) * v
            case "rmul":
                return lambda x, ctx={}, f=fn, v=a: v * f(x, ctx)
            case "truediv":
                return lambda x, ctx={}, f=fn, v=a: f(x, ctx) / v
            case "rtruediv":
                return lambda x, ctx={}, f=fn, v=a: v / f(x, ctx)
            case "floordiv":
                return lambda x, ctx={}, f=fn, v=a: f(x, ctx) // v
            case "rfloordiv":
                return lambda x, ctx={}, f=fn, v=a: v // f(x, ctx)
            case "mod":
                return lambda x, ctx={}, f=fn, v=a: f(x, ctx) % v
            case "rmod":
                return lambda x, ctx={}, f=fn, v=a: v % f(x, ctx)
            case "pow":
                return lambda x, ctx={}, f=fn, v=a: f(x, ctx) ** v
            case "rpow":
                return lambda x, ctx={}, f=fn, v=a: v ** f(x, ctx)
            case "matmul":
                return lambda x, ctx={}, f=fn, v=a: f(x, ctx) @ v
            case "rmatmul":
                return lambda x, ctx={}, f=fn, v=a: v @ f(x, ctx)
            # Một ngôi
            case "neg":
                return lambda x, ctx={}, f=fn: -f(x, ctx)
            case "pos":
                return lambda x, ctx={}, f=fn: +f(x, ctx)
            case "abs":
                return lambda x, ctx={}, f=fn: abs(f(x, ctx))
            # So sánh
            case "eq":
                return lambda x, ctx={}, f=fn, v=a: f(x, ctx) == v
            case "ne":
                return lambda x, ctx={}, f=fn, v=a: f(x, ctx) != v
            case "lt":
                return lambda x, ctx={}, f=fn, v=a: f(x, ctx) < v
            case "le":
                return lambda x, ctx={}, f=fn, v=a: f(x, ctx) <= v
            case "gt":
                return lambda x, ctx={}, f=fn, v=a: f(x, ctx) > v
            case "ge":
                return lambda x, ctx={}, f=fn, v=a: f(x, ctx) >= v
            case "contains":
                return lambda x, ctx={}, f=fn, v=a: v in f(x, ctx)
            # Áp dụng function
            case "apply":
                func, fa, kw = args
                return lambda x, ctx={}, f=fn, func=func, a=fa, k=kw: func(f(x, ctx), *a, **k)
            # Boolean operations - resolve Ref operands from context
            case "and_":
                if isinstance(a, Ref):
                    return lambda x, ctx={}, f=fn, ref=a: f(x, ctx) and ref._resolve(ctx)
                return lambda x, ctx={}, f=fn, v=a: f(x, ctx) and v
            case "rand_":
                if isinstance(a, Ref):
                    return lambda x, ctx={}, f=fn, ref=a: ref._resolve(ctx) and f(x, ctx)
                return lambda x, ctx={}, f=fn, v=a: v and f(x, ctx)
            case "or_":
                if isinstance(a, Ref):
                    return lambda x, ctx={}, f=fn, ref=a: f(x, ctx) or ref._resolve(ctx)
                return lambda x, ctx={}, f=fn, v=a: f(x, ctx) or v
            case "ror_":
                if isinstance(a, Ref):
                    return lambda x, ctx={}, f=fn, ref=a: ref._resolve(ctx) or f(x, ctx)
                return lambda x, ctx={}, f=fn, v=a: v or f(x, ctx)
            case "not_":
                return lambda x, ctx={}, f=fn: not f(x, ctx)
            case _:
                raise ValueError(f"Transform không xác định: {op}")

    def execute(self, value: Any, context: Dict[str, Any] = None) -> Any:
        """Thực thi tất cả transform trên giá trị đầu vào.

        Args:
            value: Giá trị nguồn để transform
            context: Optional dict chứa tất cả giá trị biến có sẵn,
                    dùng để resolve compound boolean operations.
                    Nếu không cung cấp, mặc định là {}.

        Returns:
            Giá trị sau khi áp dụng tất cả transform
        """
        return self._fn(value, context or {})

    def _resolve(self, ctx: Dict[str, Any]) -> Any:
        """Resolve giá trị của Ref này từ context dict.

        Dùng cho compound boolean operations khi cần resolve
        Ref operand từ context.

        Args:
            ctx: Dict chứa tất cả giá trị biến có sẵn

        Returns:
            Giá trị sau khi resolve và execute transforms
        """
        value = ctx.get(self.var)
        return self.execute(value, ctx)

    def get_all_vars(self) -> Set[str]:
        """Lấy tất cả tên biến mà Ref này phụ thuộc vào.

        Bao gồm cả biến chính (self.var) và các biến từ compound
        boolean operations (& và |).

        Returns:
            Set các tên biến

        Example:
            ref = (PARENT["a"] > 10) & (PARENT["b"] == "x") | (PARENT["c"])
            ref.get_all_vars()  # Returns {"a", "b", "c"}
        """
        vars_set = {self.var}
        for op, args in self._transforms:
            if op in ("and_", "or_", "rand_", "ror_") and args:
                other = args[0]
                if isinstance(other, Ref):
                    vars_set.update(other.get_all_vars())
        return vars_set

    def apply(self, func: Callable, *args: Any, **kwargs: Any) -> "Ref":
        """Áp dụng một function tùy chỉnh lên giá trị.

        Args:
            func: Function cần áp dụng
            *args: Các argument bổ sung cho func
            **kwargs: Các keyword argument bổ sung cho func

        Returns:
            Ref mới với transform apply
        """
        return self._with_transform("apply", func, args, kwargs)

    # =========================================================================
    # Truy cập
    # =========================================================================
    def __getitem__(self, key: Any) -> "Ref":
        return self._with_transform("getitem", key)

    def __getattr__(self, name: str) -> "Ref":
        if name.startswith("_"):
            raise AttributeError(f"'{type(self).__name__}' không có attribute '{name}'")
        return self._with_transform("getattr", name)

    def __call__(self, *args: Any, **kwargs: Any) -> "Ref":
        return self._with_transform("call", args, kwargs)

    # =========================================================================
    # Số học
    # =========================================================================
    def __add__(self, other):
        return self._with_transform("add", other)

    def __radd__(self, other):
        return self._with_transform("radd", other)

    def __sub__(self, other):
        return self._with_transform("sub", other)

    def __rsub__(self, other):
        return self._with_transform("rsub", other)

    def __mul__(self, other):
        return self._with_transform("mul", other)

    def __rmul__(self, other):
        return self._with_transform("rmul", other)

    def __truediv__(self, other):
        return self._with_transform("truediv", other)

    def __rtruediv__(self, other):
        return self._with_transform("rtruediv", other)

    def __floordiv__(self, other):
        return self._with_transform("floordiv", other)

    def __rfloordiv__(self, other):
        return self._with_transform("rfloordiv", other)

    def __mod__(self, other):
        return self._with_transform("mod", other)

    def __rmod__(self, other):
        return self._with_transform("rmod", other)

    def __pow__(self, other):
        return self._with_transform("pow", other)

    def __rpow__(self, other):
        return self._with_transform("rpow", other)

    def __matmul__(self, other):
        return self._with_transform("matmul", other)

    def __rmatmul__(self, other):
        return self._with_transform("rmatmul", other)

    # =========================================================================
    # Một ngôi
    # =========================================================================
    def __neg__(self):
        return self._with_transform("neg")

    def __pos__(self):
        return self._with_transform("pos")

    def __abs__(self):
        return self._with_transform("abs")

    # =========================================================================
    # Output Mapping (>>)
    # =========================================================================
    def __rshift__(self, other: "Ref") -> "Ref":
        """producer["output"] >> consumer["input"] hoặc producer["output"] >> PARENT["dest"].

        Dùng để map output từ producer node (self) đến consumer node hoặc PARENT.
        - node["src"] >> PARENT["dest"]: map node's src output đến graph output dest
        - producer["output"] >> consumer["input"]: map producer's output đến consumer's input

        Args:
            other: Ref đến input của consumer node hoặc PARENT

        Returns:
            other (consumer Ref) để có thể chain tiếp
        """
        if not isinstance(other, Ref):
            raise TypeError(f">> operator chỉ hỗ trợ Ref, không hỗ trợ {type(other).__name__}")

        source_node = self.raw_source  # producer node
        target_node = other.raw_source  # consumer node or PARENT

        # Check if target is PARENT["key"]
        if hasattr(target_node, "name") and target_node.name == "__PARENT__":
            # self is node["src_key"], other is PARENT["dest_key"]
            # Set node.outputs[src_key].value = Ref(parent, dest_key)
            if hasattr(source_node, "outputs") and hasattr(source_node, "parent"):
                from hush.core.utils.common import Param

                if source_node.outputs is None:
                    source_node.outputs = {}
                # Create Ref to parent graph with target key
                parent_ref = Ref(source_node.parent, other.var)
                if self.var in source_node.outputs:
                    source_node.outputs[self.var].value = parent_ref
                else:
                    source_node.outputs[self.var] = Param(value=parent_ref)
            return other

        # producer["output"] >> consumer["input"]
        # Set producer.outputs[output].value = Ref(consumer, input)
        if hasattr(source_node, "outputs"):
            from hush.core.utils.common import Param

            if source_node.outputs is None:
                source_node.outputs = {}
            # Tạo Ref đến consumer node với key đích
            consumer_ref = Ref(target_node, other.var)
            if self.var in source_node.outputs:
                source_node.outputs[self.var].value = consumer_ref
            else:
                source_node.outputs[self.var] = Param(value=consumer_ref)
        return other

    # =========================================================================
    # So sánh
    # =========================================================================
    def __lt__(self, other):
        return self._with_transform("lt", other)

    def __le__(self, other):
        return self._with_transform("le", other)

    def __gt__(self, other):
        return self._with_transform("gt", other)

    def __ge__(self, other):
        return self._with_transform("ge", other)

    def __eq__(self, other):
        return self._with_transform("eq", other)

    def __ne__(self, other):
        return self._with_transform("ne", other)

    def __contains__(self, item):
        return self._with_transform("contains", item)

    # =========================================================================
    # Boolean (compound conditions với & và |)
    # =========================================================================
    def __and__(self, other):
        return self._with_transform("and_", other)

    def __rand__(self, other):
        return self._with_transform("rand_", other)

    def __or__(self, other):
        return self._with_transform("or_", other)

    def __ror__(self, other):
        return self._with_transform("ror_", other)

    def __invert__(self):
        return self._with_transform("not_")

    # =========================================================================
    # Tiện ích
    # =========================================================================
    _TRANSFORM_SYMBOLS: dict = {
        "eq": "==",
        "ne": "!=",
        "lt": "<",
        "le": "<=",
        "gt": ">",
        "ge": ">=",
        "contains": "in",
        "add": "+",
        "sub": "-",
        "mul": "*",
        "truediv": "/",
        "floordiv": "//",
        "mod": "%",
        "and_": "and",
        "or_": "or",
        "not_": "not",
    }

    def describe(self) -> str:
        """Human-readable description of this Ref and its transforms.

        Examples:
            Ref(PARENT, "score") with transforms [("ge", (90,))]
            → "score >= 90"

            Ref(PARENT, "call_code") with transforms [("eq", ("Hua_tra",))]
            → "call_code == 'Hua_tra'"
        """
        result = self.var
        for op, args in self._transforms:
            symbol = self._TRANSFORM_SYMBOLS.get(op)
            if symbol and args:
                arg = args[0]
                result = f"{result} {symbol} {arg!r}"
            elif symbol and not args:
                result = f"{symbol} {result}"
            elif op == "getitem":
                result = f"{result}[{args[0]!r}]"
            elif op == "getattr":
                result = f"{result}.{args[0]}"
            elif op == "apply":
                func = args[0]
                fname = getattr(func, "__name__", str(func))
                result = f"{fname}({result})"
        return result

    # =========================================================================
    # Serialization
    # =========================================================================

    def serialize(self) -> dict:
        """Serialize Ref to dict for Rust backend."""
        return {
            "source": self.source,
            "var": self.var,
            "transforms": self._serialize_transforms(),
            "is_output": self.is_output,
        }

    def _serialize_transforms(self) -> list:
        """Serialize _transforms list, handling nested Refs in compound booleans."""
        result = []
        for op_name, args in self._transforms:
            serialized_args = []
            for arg in args:
                if isinstance(arg, Ref):
                    serialized_args.append({"__ref__": arg.serialize()})
                elif callable(arg) and op_name == "apply":
                    func_name = getattr(arg, "__name__", getattr(arg, "__qualname__", repr(arg)))
                    raise ValueError(
                        f"Ref.apply() with Python callable '{func_name}' cannot be serialized "
                        f"for the Rust backend. Python functions cannot cross the FFI boundary.\n"
                        f"  Ref: {self!r}\n"
                        f"Fix: Replace Ref.apply(lambda ...) with a dedicated @op(rust='...') "
                        f"that performs the same logic, then use op['result'] in your condition."
                    )
                else:
                    serialized_args.append(arg)
            result.append([op_name, serialized_args])
        return result

    def __repr__(self) -> str:
        if not self._transforms:
            return f"Ref({self.source!r}, {self.var!r})"
        return f"Ref({self.source!r}, {self.var!r}, transforms={len(self._transforms)})"
