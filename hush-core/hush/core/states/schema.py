"""StateSchema - định nghĩa cấu trúc state của workflow."""

from typing import Any, Dict, Iterator, List, Optional, Tuple, Type, TYPE_CHECKING

from hush.core.states.ref import Ref

if TYPE_CHECKING:
    from hush.core.states.state import MemoryState

__all__ = ["StateSchema"]


class StateSchema:
    """Định nghĩa cấu trúc state của workflow với độ phân giải O(1).

    Mỗi cặp (node, var) có slot duy nhất. Refs định nghĩa luồng dữ liệu:
        - pull_ref: Khi đọc biến, pull giá trị từ source (1 hop)
        - push_ref: Khi ghi biến, push giá trị đến target (1 hop)

    Cấu trúc dữ liệu:
        _var_to_idx: {(node, var): idx} - ánh xạ biến sang index
        _defaults: [value, ...] - giá trị mặc định theo index
        _pull_refs: [Ref, ...] - pull data từ source khi đọc (None nếu không pull)
        _push_refs: [Ref, ...] - push data đến target khi ghi (None nếu không push)
    """

    __slots__ = ("name", "_var_to_idx", "_defaults", "_pull_refs", "_push_refs")

    def __init__(self, node=None, name: str = None):
        """Khởi tạo schema.

        Args:
            node: Node tùy chọn để load các connection
            name: Tên workflow tùy chọn (suy ra từ node nếu không cung cấp)
        """
        self._var_to_idx: Dict[Tuple[str, str], int] = {}
        self._defaults: List[Any] = []
        self._pull_refs: List[Optional[Ref]] = []  # Pull data từ source khi đọc
        self._push_refs: List[Optional[Ref]] = []  # Push data đến target khi ghi

        self.name = name
        if node is not None:
            self.name = name or node.full_name
            self._load_from(node)
            self._build()

    # =========================================================================
    # Xây dựng Schema
    # =========================================================================

    def _load_from(self, node) -> None:
        """Thu thập đệ quy tất cả biến từ cây node.

        inputs/outputs là Dict[str, Param] với:
        - Param.value: Ref hoặc literal value
        - Param.default: giá trị mặc định
        """
        node_name = node.full_name
        inputs = node.inputs or {}
        outputs = node.outputs or {}

        # Đăng ký các biến input
        for var_name, param in inputs.items():
            # Ưu tiên value (Ref hoặc literal), sau đó là default
            if param.value is not None:
                self._register(node_name, var_name, param.value)
            else:
                self._register(node_name, var_name, param.default)

        # Đăng ký các biến output
        for var_name, param in outputs.items():
            if isinstance(param.value, Ref):
                # Push ref: node.var -> target.var (push value to target khi ghi)
                ref = param.value
                if ref.has_ops:
                    raise ValueError(f"Push ref {node_name}.{var_name} -> {ref.node}.{ref.var} không được có operation")
                self._register_push_ref(node_name, var_name, Ref(ref.node, ref.var, is_output=True))
            elif (node_name, var_name) not in self._var_to_idx:
                # Không phải push ref, đăng ký với default
                self._register(node_name, var_name, param.default)

        # Đăng ký các biến metadata
        for meta_var in ("start_time", "end_time", "error"):
            self._register(node_name, meta_var, None)

        # Load đệ quy các node con
        # Iteration nodes giờ kế thừa từ GraphNode nên được handle bởi _nodes recursion
        if hasattr(node, '_nodes') and node._nodes:
            for child in node._nodes.values():
                self._load_from(child)

    def _register(self, node: str, var: str, value: Any) -> None:
        """Đăng ký một biến (có thể gọi nhiều lần, Ref luôn được ưu tiên)."""
        key = (node, var)
        if key in self._var_to_idx:
            # Đã đăng ký - cập nhật nếu giá trị mới là Ref hoặc giá trị hiện tại là None
            idx = self._var_to_idx[key]
            current = self._defaults[idx]
            if isinstance(value, Ref) or (current is None and value is not None):
                self._defaults[idx] = value
            return

        # Biến mới - gán index
        idx = len(self._defaults)
        self._var_to_idx[key] = idx
        self._defaults.append(value)  # Có thể là Ref, được resolve trong _build()
        self._pull_refs.append(None)
        self._push_refs.append(None)

    def _register_push_ref(self, node: str, var: str, ref: Ref) -> None:
        """Đăng ký push ref (không overwrite pull ref)."""
        key = (node, var)
        if key not in self._var_to_idx:
            # Biến chưa tồn tại - đăng ký mới và set push ref
            idx = len(self._defaults)
            self._var_to_idx[key] = idx
            self._defaults.append(None)  # Không có default value
            self._pull_refs.append(None)  # Không có pull ref
            self._push_refs.append(ref)
            return
        # Biến đã tồn tại - lưu push ref riêng
        idx = self._var_to_idx[key]
        self._push_refs[idx] = ref

    def _build(self) -> None:
        """Resolve tất cả Ref sang index."""
        for key, idx in self._var_to_idx.items():
            # Resolve pull refs (Refs trong _defaults)
            value = self._defaults[idx]
            if isinstance(value, Ref):
                source_key = (value.node, value.var)
                source_idx = self._var_to_idx.get(source_key, -1)
                value.idx = source_idx  # Set source index trên Ref
                self._pull_refs[idx] = value
                self._defaults[idx] = None  # Xóa Ref, giá trị lấy từ source

            # Resolve push refs
            push_ref = self._push_refs[idx]
            if push_ref is not None:
                target_key = (push_ref.node, push_ref.var)
                target_idx = self._var_to_idx.get(target_key, -1)
                push_ref.idx = target_idx

    # =========================================================================
    # Các Method Phân Giải Core (O(1))
    # =========================================================================

    def get_index(self, node: str, var: str) -> int:
        """Lấy storage index của một biến. Trả về -1 nếu không tìm thấy."""
        return self._var_to_idx.get((node, var), -1)

    def get_pull_ref(self, idx: int) -> Optional[Ref]:
        """Lấy pull Ref cho một index (None nếu không pull)."""
        if 0 <= idx < len(self._pull_refs):
            return self._pull_refs[idx]
        return None

    def get_push_ref(self, idx: int) -> Optional[Ref]:
        """Lấy push Ref cho một index (None nếu không push)."""
        if 0 <= idx < len(self._push_refs):
            return self._push_refs[idx]
        return None

    @property
    def num_indices(self) -> int:
        """Số lượng storage index."""
        return len(self._defaults)

    # =========================================================================
    # Các Method Xây Dựng Thủ Công
    # =========================================================================

    def set(self, node: str, var: str, value: Any) -> "StateSchema":
        """Set giá trị mặc định cho một biến."""
        key = (node, var)
        if key in self._var_to_idx:
            idx = self._var_to_idx[key]
            self._defaults[idx] = value
        else:
            self._register(node, var, value)
        return self


    # =========================================================================
    # Tạo State
    # =========================================================================

    def create_state(
        self,
        inputs: Dict[str, Any] = None,
        state_class: Type["MemoryState"] = None,
        **kwargs
    ) -> "MemoryState":
        """Tạo state mới từ schema này."""
        if state_class is None:
            from hush.core.states.state import MemoryState
            state_class = MemoryState
        return state_class(schema=self, inputs=inputs, **kwargs)

    # =========================================================================
    # Collection Interface
    # =========================================================================

    def __iter__(self) -> Iterator[Tuple[str, str]]:
        """Duyệt qua tất cả cặp (node, var)."""
        return iter(self._var_to_idx.keys())

    def __getitem__(self, key: Tuple[str, str]) -> int:
        """Lấy index của (node, var). Raise KeyError nếu không tìm thấy."""
        if key in self._var_to_idx:
            return self._var_to_idx[key]
        raise KeyError(f"{key} không tìm thấy trong schema: {self.name}")

    def __contains__(self, key: Tuple[str, str]) -> bool:
        """Kiểm tra (node, var) đã được đăng ký chưa."""
        return key in self._var_to_idx

    def __len__(self) -> int:
        """Số lượng biến đã đăng ký."""
        return len(self._var_to_idx)

    def __repr__(self) -> str:
        pull = sum(1 for ref in self._pull_refs if ref is not None)
        push = sum(1 for ref in self._push_refs if ref is not None)
        return f"StateSchema(name='{self.name}', vars={len(self._var_to_idx)}, pull={pull}, push={push})"

    # =========================================================================
    # Debug
    # =========================================================================

    @staticmethod
    def _ops_to_str(ops: List[Tuple[str, Any]]) -> str:
        """Chuyển đổi danh sách ops thành string dễ đọc như x['key'].upper()"""
        result = "x"
        for op, args in ops:
            a = args[0] if args else None
            match op:
                case 'getitem': result += f"[{a!r}]"
                case 'getattr': result += f".{a}"
                case 'call':
                    ca, kw = args
                    args_str = ", ".join([repr(x) for x in ca] + [f"{k}={v!r}" for k, v in kw.items()])
                    result += f"({args_str})"
                case 'add': result = f"({result} + {a!r})"
                case 'radd': result = f"({a!r} + {result})"
                case 'sub': result = f"({result} - {a!r})"
                case 'rsub': result = f"({a!r} - {result})"
                case 'mul': result = f"({result} * {a!r})"
                case 'rmul': result = f"({a!r} * {result})"
                case 'truediv': result = f"({result} / {a!r})"
                case 'rtruediv': result = f"({a!r} / {result})"
                case 'floordiv': result = f"({result} // {a!r})"
                case 'rfloordiv': result = f"({a!r} // {result})"
                case 'mod': result = f"({result} % {a!r})"
                case 'rmod': result = f"({a!r} % {result})"
                case 'pow': result = f"({result} ** {a!r})"
                case 'rpow': result = f"({a!r} ** {result})"
                case 'neg': result = f"(-{result})"
                case 'apply':
                    func, _, _ = args
                    func_name = getattr(func, '__name__', repr(func))
                    result = f"{func_name}({result})"
                case _: result += f".{op}(...)"
        return result

    def show(self) -> None:
        """Hiển thị debug cấu trúc schema."""
        print(f"\n=== StateSchema: {self.name} ===")

        # Xây dựng reverse index cho hiển thị
        idx_to_key = {idx: key for key, idx in self._var_to_idx.items()}

        for node, var in self:
            idx = self._var_to_idx[(node, var)]
            pull_ref = self._pull_refs[idx]
            push_ref = self._push_refs[idx]
            default = self._defaults[idx]

            parts = [f"{node}.{var} [{idx}]"]

            if pull_ref is not None:
                source_key = idx_to_key.get(pull_ref.idx, ("?", "?"))
                ops_str = f" {self._ops_to_str(pull_ref._ops)}" if pull_ref.has_ops else ""
                parts.append(f"<- pull {source_key[0]}.{source_key[1]}[{pull_ref.idx}]{ops_str}")

            if push_ref is not None:
                target_key = idx_to_key.get(push_ref.idx, ("?", "?"))
                parts.append(f"-> push {target_key[0]}.{target_key[1]}[{push_ref.idx}]")

            if pull_ref is None and push_ref is None:
                parts.append(f"= {default}")

            print(" ".join(parts))

        print(f"Tổng: {len(self._defaults)} biến")
