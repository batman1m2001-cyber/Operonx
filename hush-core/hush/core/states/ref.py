"""Kiểu Ref cho liên kết biến zero-copy với khả năng chain operation."""

from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Set, Tuple, Union

if TYPE_CHECKING:
    from hush.core.nodes.base import BaseNode

__all__ = ["Ref"]


class Ref:
    """Tham chiếu đến biến khác với khả năng chain các operation.

    Các operation được ghi lại và compile thành callable để thực thi nhanh.
    Ref cho phép truy cập dữ liệu từ node khác mà không cần copy,
    đồng thời hỗ trợ transform dữ liệu thông qua các operation như
    getitem, getattr, arithmetic, comparison, v.v.

    Supports compound boolean operations với & và |:
        (PARENT["x"] > 10) & (PARENT["y"] == "active")
        (node["a"]) | (node["b"])
        ~(node["flag"])  # negation

    Attributes:
        _node: Node nguồn (có thể là string hoặc BaseNode)
        var: Tên biến nguồn
        _ops: Danh sách các operation đã ghi
        _fn: Function đã compile từ ops (signature: fn(value, context) -> result)
        idx: Index trong schema (được set bởi StateSchema._build())
        is_output: True nếu đây là output ref (đẩy giá trị ra ngoài)
    """

    __slots__ = ("_node", "var", "_ops", "_fn", "idx", "is_output")

    _RESERVED_ATTRS = frozenset(
        {
            "_node",
            "var",
            "_ops",
            "_fn",
            "idx",
            "is_output",
            "node",
            "raw_node",
            "ops",
            "as_tuple",
            "apply",
            "execute",
            "has_ops",
            "_with_op",
            "_clone",
            "_resolve",
            "get_all_vars",
        }
    )

    def __init__(
        self,
        node: Union["BaseNode", str],
        var: str,
        _ops: Optional[List[Tuple[str, Any]]] = None,
        _fn: Optional[Callable] = None,
        is_output: bool = False,
    ) -> None:
        """Khởi tạo Ref.

        Args:
            node: Node nguồn (BaseNode hoặc string tên node)
            var: Tên biến nguồn
            _ops: Danh sách operation (dùng cho deserialization)
            _fn: Function đã compile (dùng cho clone)
            is_output: True nếu là output ref
        """
        object.__setattr__(self, "_node", node)
        object.__setattr__(self, "var", var)
        object.__setattr__(self, "_ops", _ops or [])
        object.__setattr__(self, "idx", -1)  # Được set bởi StateSchema._build()
        object.__setattr__(self, "is_output", is_output)  # True cho output ref
        # Nếu có ops nhưng không có fn, rebuild từ ops (trường hợp deserialization)
        if _fn is None and _ops:

            def _fn(x, ctx={}):
                return x

            for op, args in _ops:
                _fn = self._wrap(_fn, op, args)
        object.__setattr__(self, "_fn", _fn or (lambda x, ctx={}: x))

    @property
    def node(self) -> str:
        """Tên đầy đủ của node nguồn."""
        return self._node.full_name if hasattr(self._node, "full_name") else self._node

    @property
    def raw_node(self) -> Union["BaseNode", str]:
        """Node nguồn gốc (có thể là object hoặc string)."""
        return self._node

    @property
    def ops(self) -> List[Tuple[str, Tuple[Any, ...]]]:
        """Danh sách các operation đã ghi."""
        return self._ops

    @property
    def has_ops(self) -> bool:
        """Kiểm tra có operation nào không."""
        return len(self._ops) > 0

    def as_tuple(self) -> Tuple[str, str]:
        """Trả về tuple (node_name, var_name)."""
        return (self.node, self.var)

    def _clone(self) -> "Ref":
        """Tạo bản sao của Ref."""
        return Ref(self._node, self.var, list(self._ops), self._fn, self.is_output)

    def _with_op(self, op: str, *args: Any) -> "Ref":
        """Tạo Ref mới với thêm một operation."""
        new_ops = self._ops + [(op, args)]
        new_fn = self._wrap(self._fn, op, args)
        return Ref(self._node, self.var, new_ops, new_fn)

    @staticmethod
    def _wrap(fn: Callable, op: str, args: Tuple) -> Callable:
        """Wrap function với thêm một operation.

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
                raise ValueError(f"Operation không xác định: {op}")

    def execute(self, value: Any, context: Dict[str, Any] = None) -> Any:
        """Thực thi tất cả operation trên giá trị đầu vào.

        Args:
            value: Giá trị nguồn để transform
            context: Optional dict chứa tất cả giá trị biến có sẵn,
                    dùng để resolve compound boolean operations.
                    Nếu không cung cấp, mặc định là {}.

        Returns:
            Giá trị sau khi áp dụng tất cả operation
        """
        return self._fn(value, context or {})

    def _resolve(self, ctx: Dict[str, Any]) -> Any:
        """Resolve giá trị của Ref này từ context dict.

        Dùng cho compound boolean operations khi cần resolve
        Ref operand từ context.

        Args:
            ctx: Dict chứa tất cả giá trị biến có sẵn

        Returns:
            Giá trị sau khi resolve và execute operations
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
        for op, args in self._ops:
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
            Ref mới với operation apply
        """
        return self._with_op("apply", func, args, kwargs)

    # =========================================================================
    # Truy cập
    # =========================================================================
    def __getitem__(self, key: Any) -> "Ref":
        return self._with_op("getitem", key)

    def __getattr__(self, name: str) -> "Ref":
        if name.startswith("_"):
            raise AttributeError(f"'{type(self).__name__}' không có attribute '{name}'")
        return self._with_op("getattr", name)

    def __call__(self, *args: Any, **kwargs: Any) -> "Ref":
        return self._with_op("call", args, kwargs)

    # =========================================================================
    # Số học
    # =========================================================================
    def __add__(self, other):
        return self._with_op("add", other)

    def __radd__(self, other):
        return self._with_op("radd", other)

    def __sub__(self, other):
        return self._with_op("sub", other)

    def __rsub__(self, other):
        return self._with_op("rsub", other)

    def __mul__(self, other):
        return self._with_op("mul", other)

    def __rmul__(self, other):
        return self._with_op("rmul", other)

    def __truediv__(self, other):
        return self._with_op("truediv", other)

    def __rtruediv__(self, other):
        return self._with_op("rtruediv", other)

    def __floordiv__(self, other):
        return self._with_op("floordiv", other)

    def __rfloordiv__(self, other):
        return self._with_op("rfloordiv", other)

    def __mod__(self, other):
        return self._with_op("mod", other)

    def __rmod__(self, other):
        return self._with_op("rmod", other)

    def __pow__(self, other):
        return self._with_op("pow", other)

    def __rpow__(self, other):
        return self._with_op("rpow", other)

    def __matmul__(self, other):
        return self._with_op("matmul", other)

    def __rmatmul__(self, other):
        return self._with_op("rmatmul", other)

    # =========================================================================
    # Một ngôi
    # =========================================================================
    def __neg__(self):
        return self._with_op("neg")

    def __pos__(self):
        return self._with_op("pos")

    def __abs__(self):
        return self._with_op("abs")

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

        source_node = self.raw_node  # producer node
        target_node = other.raw_node  # consumer node hoặc PARENT

        # Kiểm tra nếu target là PARENT["key"]
        if hasattr(target_node, "name") and target_node.name == "__PARENT__":
            # self là node["src_key"], other là PARENT["dest_key"]
            # Set node.outputs[src_key].value = Ref(father, dest_key)
            if hasattr(source_node, "outputs") and hasattr(source_node, "father"):
                from hush.core.utils.common import Param

                if source_node.outputs is None:
                    source_node.outputs = {}
                # Tạo Ref đến father (graph cha) với key đích
                father_ref = Ref(source_node.father, other.var)
                if self.var in source_node.outputs:
                    source_node.outputs[self.var].value = father_ref
                else:
                    source_node.outputs[self.var] = Param(value=father_ref)
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
        return self._with_op("lt", other)

    def __le__(self, other):
        return self._with_op("le", other)

    def __gt__(self, other):
        return self._with_op("gt", other)

    def __ge__(self, other):
        return self._with_op("ge", other)

    def __eq__(self, other):
        return self._with_op("eq", other)

    def __ne__(self, other):
        return self._with_op("ne", other)

    def __contains__(self, item):
        return self._with_op("contains", item)

    # =========================================================================
    # Boolean (compound conditions với & và |)
    # =========================================================================
    def __and__(self, other):
        return self._with_op("and_", other)

    def __rand__(self, other):
        return self._with_op("rand_", other)

    def __or__(self, other):
        return self._with_op("or_", other)

    def __ror__(self, other):
        return self._with_op("ror_", other)

    def __invert__(self):
        return self._with_op("not_")

    # =========================================================================
    # Tiện ích
    # =========================================================================
    def __repr__(self) -> str:
        if not self._ops:
            return f"Ref({self.node!r}, {self.var!r})"
        return f"Ref({self.node!r}, {self.var!r}, ops={len(self._ops)})"
