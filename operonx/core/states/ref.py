"""Ref type for zero-copy variable references with chainable transforms."""

import operator
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Set, Tuple, Union

if TYPE_CHECKING:
    from operonx.core.ops.base import BaseOp

__all__ = ["Ref"]

# Transforms of the form ``value <op> operand``. ``contains`` is
# ``operand in value``, which is ``operator.contains(value, operand)``.
_BINARY_OPS: Dict[str, Callable[[Any, Any], Any]] = {
    "add": operator.add,
    "sub": operator.sub,
    "mul": operator.mul,
    "truediv": operator.truediv,
    "floordiv": operator.floordiv,
    "mod": operator.mod,
    "pow": operator.pow,
    "matmul": operator.matmul,
    "eq": operator.eq,
    "ne": operator.ne,
    "lt": operator.lt,
    "le": operator.le,
    "gt": operator.gt,
    "ge": operator.ge,
    "contains": operator.contains,
}

# Reflected transforms: ``operand <op> value`` (``1 - ref``).
_REFLECTED_OPS: Dict[str, Callable[[Any, Any], Any]] = {
    "radd": operator.add,
    "rsub": operator.sub,
    "rmul": operator.mul,
    "rtruediv": operator.truediv,
    "rfloordiv": operator.floordiv,
    "rmod": operator.mod,
    "rpow": operator.pow,
    "rmatmul": operator.matmul,
}


def _getattr_or_key(value: Any, name: str) -> Any:
    """``ref.name`` at run time: the attribute, else the key of a mapping.

    Op outputs are usually dicts, so ``src["obj"].name`` reads the
    ``"name"`` key rather than raising. A real attribute wins, so
    ``ref.get("k")`` and ``ref.items()`` stay method calls.
    """
    try:
        return getattr(value, name)
    except AttributeError:
        if isinstance(value, Mapping) and name in value:
            return value[name]
        raise


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
            "get_all_refs",
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
        Context maps ``(source, var)`` to the value of every Ref the
        expression reads (see ``_resolve``). A Ref among a transform's
        arguments (``a >= b``, ``a + b``, ``d[k]``, ``a & b``) is read from
        it when the transform runs — captured as-is, ``a >= b`` would build
        another Ref, which is truthy, and a branch on it would always take
        its first case. Context defaults to {} for backward compatibility.
        """
        a = args[0] if args else None

        if op in _BINARY_OPS:
            g = _BINARY_OPS[op]
            if isinstance(a, Ref):
                return lambda x, ctx={}, f=fn, g=g, r=a: g(f(x, ctx), r._resolve(ctx))
            return lambda x, ctx={}, f=fn, g=g, v=a: g(f(x, ctx), v)
        if op in _REFLECTED_OPS:
            g = _REFLECTED_OPS[op]
            if isinstance(a, Ref):
                return lambda x, ctx={}, f=fn, g=g, r=a: g(r._resolve(ctx), f(x, ctx))
            return lambda x, ctx={}, f=fn, g=g, v=a: g(v, f(x, ctx))

        match op:
            # Truy cập
            case "getitem":
                if isinstance(a, Ref):
                    return lambda x, ctx={}, f=fn, r=a: f(x, ctx)[r._resolve(ctx)]
                return lambda x, ctx={}, f=fn, k=a: f(x, ctx)[k]
            case "getattr":
                return lambda x, ctx={}, f=fn, k=a: _getattr_or_key(f(x, ctx), k)
            case "call":
                ca, kw = args
                if Ref._any_ref(ca, kw):
                    return lambda x, ctx={}, f=fn, a=ca, k=kw: f(x, ctx)(
                        *Ref._read_args(a, ctx), **Ref._read_kwargs(k, ctx)
                    )
                return lambda x, ctx={}, f=fn, a=ca, k=kw: f(x, ctx)(*a, **k)
            # Một ngôi
            case "neg":
                return lambda x, ctx={}, f=fn: -f(x, ctx)
            case "pos":
                return lambda x, ctx={}, f=fn: +f(x, ctx)
            case "abs":
                return lambda x, ctx={}, f=fn: abs(f(x, ctx))
            # Áp dụng function
            case "apply":
                func, fa, kw = args
                if Ref._any_ref(fa, kw):
                    return lambda x, ctx={}, f=fn, func=func, a=fa, k=kw: func(
                        f(x, ctx), *Ref._read_args(a, ctx), **Ref._read_kwargs(k, ctx)
                    )
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

    @staticmethod
    def _any_ref(args: Tuple, kwargs: Dict[str, Any]) -> bool:
        return any(isinstance(v, Ref) for v in (*args, *kwargs.values()))

    @staticmethod
    def _read_args(args: Tuple, ctx: Dict[Any, Any]) -> List[Any]:
        return [v._resolve(ctx) if isinstance(v, Ref) else v for v in args]

    @staticmethod
    def _read_kwargs(kwargs: Dict[str, Any], ctx: Dict[Any, Any]) -> Dict[str, Any]:
        return {k: v._resolve(ctx) if isinstance(v, Ref) else v for k, v in kwargs.items()}

    @staticmethod
    def _arg_values(op: str, args: Tuple) -> Tuple[Any, ...]:
        """A transform's arguments, flattened: ``call`` and ``apply`` carry
        theirs in a tuple and a dict rather than as ``args`` itself."""
        if op == "call":
            ca, kw = args
            return (*ca, *kw.values())
        if op == "apply":
            _func, fa, kw = args
            return (*fa, *kw.values())
        return args

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

    def _ctx_key(self) -> Tuple[Any, str]:
        """The key this Ref's value has in a condition context.

        The source op itself, not its name: two ops that both output ``n``
        must not share a slot, and an op's full name changes when its graph
        is nested, while the object does not.
        """
        return (self._source, self.var)

    def _resolve(self, ctx: Dict[Any, Any]) -> Any:
        """Read this Ref's value from a condition context and run its transforms.

        The context is keyed by ``(source, var)`` — see ``_ctx_key``. A
        context keyed by bare variable name, as a caller of ``execute``
        may build by hand, is still read when the key is absent.

        Args:
            ctx: Dict chứa tất cả giá trị biến có sẵn

        Returns:
            Giá trị sau khi resolve và execute transforms
        """
        key = self._ctx_key()
        value = ctx[key] if key in ctx else ctx.get(self.var)
        return self.execute(value, ctx)

    def get_all_refs(self) -> List["Ref"]:
        """Every value this Ref reads: its own and each Ref among its
        transforms' arguments, recursively.

        Returns:
            One plain ``Ref(source, var)`` per ``(source, var)``, in the
            order first met.

        Example:
            ref = (a["n"] > 10) & (b["n"] >= a["limit"])
            ref.get_all_refs()  # [Ref(a, "n"), Ref(b, "n"), Ref(a, "limit")]
        """
        found: Dict[Tuple[Any, str], Ref] = {}

        def visit(ref: "Ref") -> None:
            key = ref._ctx_key()
            if key not in found:
                found[key] = Ref(ref._source, ref.var)
            for op, args in ref._transforms:
                for v in Ref._arg_values(op, args):
                    if isinstance(v, Ref):
                        visit(v)

        visit(self)
        return list(found.values())

    def get_all_vars(self) -> Set[str]:
        """Lấy tất cả tên biến mà Ref này phụ thuộc vào.

        Names only — two sources with the same variable name give one
        entry; ``get_all_refs`` keeps them apart.

        Returns:
            Set các tên biến

        Example:
            ref = (PARENT["a"] > 10) & (PARENT["b"] == "x") | (PARENT["c"])
            ref.get_all_vars()  # Returns {"a", "b", "c"}
        """
        return {ref.var for ref in self.get_all_refs()}

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

    # Without this, `__getitem__` makes a Ref iterable by the legacy
    # protocol — `ref[0]`, `ref[1]`, … never an IndexError — so `list(ref)`
    # never returns. None makes `iter()` raise "not iterable" at once.
    __iter__ = None

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
                from operonx.core.utils.common import Param

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
            from operonx.core.utils.common import Param

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

    def __bool__(self):
        """A Ref has no truth value while the graph is being built.

        ``and``, ``or``, ``not``, ``if`` and ``in`` all ask for one, and an
        object without ``__bool__`` is always truthy — so
        ``x == 1 and y == 2`` silently became ``y == 2``, and a bare
        ``if ref:`` always passed. Refuse, and say what to write instead.
        Code that means "is there a Ref" writes ``ref is not None``.
        """
        raise TypeError(
            f"{self!r} has no truth value while the graph is being built, so "
            f"`and`, `or`, `not`, `if` and `in` cannot see inside it. Combine "
            f"conditions with `&`, `|` and `~` — `(x == 1) & (y == 2)` — and "
            f"compute anything else in an op."
        )

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
                shown = arg.describe() if isinstance(arg, Ref) else repr(arg)
                result = f"{result} {symbol} {shown}"
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
