"""Base class cho tất cả các node trong workflow."""

import asyncio
import traceback
import uuid
from abc import ABC
from datetime import datetime
from time import perf_counter
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from hush.core.configs.node_config import NodeType
from hush.core.loggings import LOGGER, format_log_data
from hush.core.states.ref import Ref
from hush.core.utils.common import Param, _auto_name, unique_name
from hush.core.utils.context import get_current

if TYPE_CHECKING:
    from hush.core.states import MemoryState


class SoftEdge:
    """Marker wrapper cho soft edge connection.

    Sử dụng với cú pháp ~node để đánh dấu soft edge:
        a >> ~b  # soft edge từ a đến b
        a >> b   # hard edge từ a đến b
        [a, b] >> ~c  # soft edges từ nhiều nodes
        ~a >> b  # soft edge, rồi tiếp tục chain

    Soft edge không tính vào ready_count, dùng cho branch outputs
    khi chỉ một nhánh được thực thi.
    """

    __slots__ = ["node"]

    def __init__(self, node: "BaseNode"):
        self.node = node

    @property
    def name(self) -> str:
        return self.node.name

    def __rrshift__(self, other):
        """[a, b] >> ~self: soft edges từ list nodes đến self.node."""
        add_edge = getattr(self.node.father, "add_edge", None)

        if isinstance(other, list):
            if add_edge is not None:
                for node in other:
                    edge_type = "condition" if getattr(node, "type", None) == "branch" else "normal"
                    add_edge(node.name, self.node.name, edge_type, soft=True)
            return self.node  # Return unwrapped for chaining
        elif getattr(other, "name", None) is not None:
            # single_node >> ~self
            edge_type = "condition" if getattr(other, "type", None) == "branch" else "normal"
            if add_edge is not None:
                add_edge(other.name, self.node.name, edge_type, soft=True)
            return self.node
        return NotImplemented

    def __rshift__(self, other):
        """~a >> b: sau soft edge marker, tiếp tục với hard edge.

        Cho phép chaining: source >> ~a >> b >> c
        Sau khi ~a được xử lý, cần tiếp tục chain với b.
        """
        return self.node.__rshift__(other)


def _has_explicit_outputs(node) -> bool:
    """Kiểm tra node có outputs được user định nghĩa explicit hay không.

    Returns True nếu:
    - outputs là None (chưa set)
    - outputs rỗng
    - outputs chỉ có các Param với value=None (auto-parsed từ function)

    Returns False nếu có bất kỳ output nào có value != None (user set).
    """
    if not hasattr(node, "outputs") or node.outputs is None:
        return False
    if len(node.outputs) == 0:
        return False
    # Kiểm tra nếu có output nào có value (user đã set explicit)
    for param in node.outputs.values():
        if hasattr(param, "value") and param.value is not None:
            return True
    return False


# Base init keys shared by ALL nodes (from BaseNode.__init__)
_BASE_INIT_KEYS = frozenset(
    {
        "name",
        "id",
        "description",
        "inputs",
        "outputs",
        "sources",
        "targets",
        "stream",
        "start",
    }
)


def split_shorthand_kwargs(kwargs: dict, extra_init_keys: set = None) -> tuple:
    """Split flat kwargs into (inputs, init_kwargs).

    Used by shorthand functions (llm_, for_, code_node, etc.) to separate
    node constructor kwargs from input mappings.

    Args:
        kwargs: Flat keyword arguments from shorthand function
        extra_init_keys: Additional node-specific init keys beyond base keys
            (e.g., {'max_concurrency', 'callback'} for iteration nodes)

    Returns:
        (inputs, init_kwargs) tuple where:
        - inputs: Dict of input variable mappings
        - init_kwargs: Dict of node constructor arguments

    Example:
        # Provider nodes - just base keys
        inputs, init_kwargs = split_shorthand_kwargs(kwargs)

        # Iteration nodes - with extra keys
        inputs, init_kwargs = split_shorthand_kwargs(
            kwargs,
            {'max_concurrency', 'stop_condition', 'callback'}
        )
    """
    init_keys = _BASE_INIT_KEYS | (extra_init_keys or set())

    inputs = {}
    init_kwargs = {}

    for key, value in kwargs.items():
        if key in init_keys and not isinstance(value, Ref):
            init_kwargs[key] = value
        else:
            if key in init_keys:
                LOGGER.warning(
                    "Keyword '%s' is a reserved node parameter (one of %s) but received a Ref value. "
                    "It will be treated as an input mapping instead of a node constructor arg. "
                    "Consider renaming this parameter to avoid ambiguity.",
                    key,
                    sorted(init_keys),
                )
            inputs[key] = value

    return inputs, init_kwargs


class BaseNode(ABC):
    """Base class cho tất cả các node trong workflow.

    Node là đơn vị xử lý cơ bản trong workflow. Mỗi node có:
    - inputs: Dict[str, Param] - định nghĩa và kết nối biến đầu vào
    - outputs: Dict[str, Param] - định nghĩa và kết nối biến đầu ra
    - core: Function thực thi logic chính

    Mỗi Param chứa:
    - type: Kiểu dữ liệu (tự động infer nếu không chỉ định)
    - required: Có bắt buộc hay không
    - default: Giá trị mặc định
    - description: Mô tả
    - value: Ref hoặc literal value

    Hỗ trợ kết nối node bằng operators:
    - >>  : Hard edge (kết nối tuần tự, tính vào ready_count)
    - >>~ : Soft edge (kết nối điều kiện, không tính vào ready_count)

    Ví dụ:
        # Hard edges (normal flow)
        START >> a >> b >> c >> END

        # Soft edges (branch outputs) - dùng ~node
        branch >> ~case1 >> merge
        branch >> ~case2 >> merge

        # Mixed chain
        a >> ~b >> c >> ~d >> e
        # a->b soft, b->c hard, c->d soft, d->e hard
    """

    INNER_PROCESS = "__inner__"

    __slots__ = [
        "id",
        "name",
        "description",
        "type",
        "stream",
        "start",
        "end",
        "verbose",
        "sources",
        "targets",
        "inputs",
        "outputs",
        "core",
        "father",
        "contain_generation",
        "enabled",
    ]

    def __init__(
        self,
        id: str = None,
        name: str = None,
        description: str = "",
        inputs: Dict[str, Any] = None,
        outputs: Dict[str, Any] = None,
        sources: List[str] = None,
        targets: List[str] = None,
        stream: bool = False,
        start: bool = False,
        end: bool = False,
        contain_generation: bool = False,
        verbose: bool = True,
        enabled: bool = True,
    ):
        self.id = id or uuid.uuid4().hex
        if name is None:
            name = _auto_name()
        self.name = name or unique_name()
        self.description = description

        self.stream = stream
        self.start = start
        self.end = end
        self.verbose = verbose
        self.enabled = enabled

        self.sources: List[str] = sources or []
        self.targets: List[str] = targets or []

        self.core: Optional[Callable] = None
        self.contain_generation = contain_generation
        # Đăng ký vào graph cha
        self.father = get_current()
        # Use getattr to avoid hasattr's double lookup
        add_node = getattr(self.father, "add_node", None)
        if add_node is not None:
            add_node(self)

        # Validate tên node
        if self.name and not self.name.replace("_", "").replace("-", "").isalnum():
            raise ValueError(
                f"Tên node '{self.name}' chỉ được chứa ký tự alphanumeric, underscore và hyphen"
            )

        # Chuẩn hóa inputs/outputs thành Dict[str, Param]
        self.inputs: Dict[str, Param] = self._normalize_params(inputs)
        self.outputs: Dict[str, Param] = self._normalize_params(outputs)

        # Lỗi nếu có key trùng trong cả inputs và outputs
        if self.inputs and self.outputs:
            overlapping_keys = set(self.inputs.keys()) & set(self.outputs.keys())
            if overlapping_keys:
                raise ValueError(
                    f"Node '{self.name}' có key trùng giữa input/output: {overlapping_keys}. "
                    "Tên biến input và output phải khác nhau."
                )

    def _resolve_value(self, key: str, value: Any) -> Any:
        """Chuyển đổi value thành Ref hoặc giữ nguyên literal.

        Các format được hỗ trợ:
            - some_node → Ref(some_node, key)
            - some_node["other"] → Ref(some_node, "other")
            - Ref(node, "var") → giữ nguyên
            - PARENT["x"] → Ref(father, "x")
            - literal → giữ nguyên
        """

        def resolve_node(node):
            """Resolve PARENT thành father node."""
            if hasattr(node, "name") and node.name == "__PARENT__":
                return self.father if self.father else node
            return node

        # Xử lý Ref trực tiếp - giữ nguyên operations
        if isinstance(value, Ref):
            resolved = resolve_node(value.raw_node)
            return Ref(resolved, value.var, value.ops)

        # Xử lý node reference: some_node → Ref(some_node, key)
        if hasattr(value, "name"):
            resolved = resolve_node(value)
            return Ref(resolved, key)

        # Giá trị literal
        return value

    def _normalize_params(self, params: Any) -> Dict[str, Param]:
        """Chuẩn hóa inputs/outputs thành Dict[str, Param].

        Các format được hỗ trợ:
            - params=None → {}
            - params={"*": PARENT} → forward tất cả keys từ PARENT
            - params={"x": 1, "*": PARENT} → x=1, còn lại từ PARENT
            - params={"var": Param(...)} → giữ nguyên, resolve value
            - params={"var": some_node} → {"var": Param(value=Ref(some_node, "var"))}
            - params={"var": some_node["other"]} → {"var": Param(value=Ref(some_node, "other"))}
            - params={"var": literal} → {"var": Param(value=literal)}
            - params={("a", "b"): some_node} → mở rộng thành cả hai keys
        """
        if params is None:
            return {}

        result = {}

        if isinstance(params, dict):
            for key, value in params.items():
                # Xử lý wildcard "*" key - store for later processing in _merge_params
                if key == "*":
                    # Validate that value is a node reference (PARENT or another node)
                    if hasattr(value, "name"):
                        result["__FORWARD_WILDCARD__"] = value
                    else:
                        raise ValueError(
                            f"Wildcard '*' key phải có value là node reference (như PARENT), "
                            f"nhận được: {type(value)}"
                        )
                    continue

                # Xử lý tuple keys: {("a", "b"): node} → mở rộng thành cả hai
                if isinstance(key, tuple):
                    for k in key:
                        resolved_value = self._resolve_value(k, value)
                        result[k] = Param(value=resolved_value)
                # Xử lý Param trực tiếp
                elif isinstance(value, Param):
                    # Resolve value trong Param nếu có
                    if value.value is not None:
                        value.value = self._resolve_value(key, value.value)
                    result[key] = value
                else:
                    # Tạo Param mới với value đã resolve (type auto-inferred)
                    resolved_value = self._resolve_value(key, value)
                    result[key] = Param(value=resolved_value)

        return result

    def _merge_params(
        self, schema: Dict[str, Param], user_provided: Dict[str, Any]
    ) -> Dict[str, Param]:
        """Merge schema (từ parsing) với user-provided inputs/outputs.

        - Nếu key đã tồn tại trong schema → chỉ gán value
        - Nếu key mới → tạo Param mới (type auto-inferred)
        - Nếu user_provided có marker __FORWARD_WILDCARD__ → forward các keys
          chưa được định nghĩa explicitly đến node reference (PARENT)

        Args:
            schema: Dict[str, Param] từ parsing (ví dụ: từ function signature)
            user_provided: Dict từ user (Ref | literal | Param)

        Returns:
            Dict[str, Param] đã merge
        """
        # Copy schema để không mutate original
        result = {
            k: Param(
                type=v.type,
                required=v.required,
                default=v.default,
                description=v.description,
                value=v.value,
            )
            for k, v in schema.items()
        }

        # Resolve PARENT refs in schema values
        for key, param in result.items():
            if isinstance(param.value, Ref):
                param.value = self._resolve_value(key, param.value)

        if not user_provided:
            return result

        # Make a copy to avoid mutating the original dict
        user_provided = dict(user_provided)

        # Extract wildcard node if present
        wildcard_node = user_provided.pop("__FORWARD_WILDCARD__", None)

        # First, process explicitly provided keys
        explicitly_set = set()
        for key, value in user_provided.items():
            # Xử lý tuple keys
            if isinstance(key, tuple):
                for k in key:
                    self._merge_single_param(result, k, value)
                    explicitly_set.add(k)
            else:
                self._merge_single_param(result, key, value)
                explicitly_set.add(key)

        # Then, apply wildcard forwarding for remaining keys
        if wildcard_node is not None:
            # Resolve PARENT thành father
            if hasattr(wildcard_node, "name") and wildcard_node.name == "__PARENT__":
                resolved_node = self.father if self.father else wildcard_node
            else:
                resolved_node = wildcard_node

            # Forward schema keys that weren't explicitly set
            for key in result:
                if key not in explicitly_set:
                    result[key].value = Ref(resolved_node, key)

        return result

    def _merge_single_param(self, result: Dict[str, Param], key: str, value: Any) -> None:
        """Merge một param vào result dict."""
        if key in result:
            # Key đã tồn tại → chỉ gán value
            if isinstance(value, Param):
                result[key].value = (
                    self._resolve_value(key, value.value) if value.value is not None else None
                )
            else:
                result[key].value = self._resolve_value(key, value)
        else:
            # Key mới → tạo Param mới (type auto-inferred in Param.__post_init__)
            if isinstance(value, Param):
                if value.value is not None:
                    value.value = self._resolve_value(key, value.value)
                result[key] = value
            else:
                resolved_value = self._resolve_value(key, value)
                result[key] = Param(value=resolved_value)

    @property
    def full_name(self) -> str:
        """Đường dẫn phân cấp đầy đủ của node."""
        if self.father:
            return f"{self.father.full_name}.{self.name}"
        return self.name

    def identity(self, context_id: str) -> str:
        """Đường dẫn đầy đủ kèm context_id."""
        return f"{self.full_name}[{context_id or 'main'}]"

    def __getitem__(self, item) -> "Ref":
        """Cho phép cú pháp node["var"] để tham chiếu output.

        - node["var"] → Ref đến output cụ thể
        """
        return Ref(self, item)

    def __invert__(self) -> "SoftEdge":
        """~node: Đánh dấu node này cho soft edge connection.

        Sử dụng:
            a >> ~b  # soft edge từ a đến b
            a >> b   # hard edge từ a đến b

        Returns:
            SoftEdge wrapper chứa node này
        """
        return SoftEdge(self)

    def __rshift__(self, other):
        """node >> other: kết nối node này đến other.

        Hỗ trợ:
            a >> b      # hard edge
            a >> ~b     # soft edge (b wrapped trong SoftEdge)
            a >> [b, c] # hard edges đến nhiều nodes
            a >> END    # auto-set outputs wildcard
        """
        edge_type = "condition" if self.type == "branch" else "normal"
        add_edge = getattr(self.father, "add_edge", None)

        # Handle SoftEdge marker: a >> ~b
        if isinstance(other, SoftEdge):
            if add_edge is not None:
                add_edge(self.name, other.node.name, edge_type, soft=True)
            return other.node  # Return unwrapped node for chaining

        if isinstance(other, list):
            if add_edge is not None:
                for node in other:
                    # Check if item is SoftEdge
                    if isinstance(node, SoftEdge):
                        add_edge(self.name, node.node.name, edge_type, soft=True)
                    else:
                        add_edge(self.name, node.name, edge_type)
            return other
        elif getattr(other, "name", None) is not None:
            # Check if other is END - auto-set wildcard outputs
            if getattr(other, "name", None) == "__END__":
                if not _has_explicit_outputs(self):
                    if self.outputs is None:
                        self.outputs = {}
                    father = getattr(self, "father", None) or PARENT
                    for key in self.outputs:
                        param = self.outputs[key]
                        # Only update if param is a Param with value=None
                        if hasattr(param, "value") and param.value is None:
                            param.value = Ref(father, key)
            if add_edge is not None:
                add_edge(self.name, other.name, edge_type)
            return other
        return NotImplemented

    def __rrshift__(self, other):
        """[n1, n2] >> self: kết nối list nodes đến self."""
        edge_type = "condition" if self.type == "branch" else "normal"
        add_edge = getattr(self.father, "add_edge", None)

        if isinstance(other, list):
            if add_edge is not None:
                for node in other:
                    add_edge(node.name, self.name, edge_type)
        return self

    def __gt__(self, other):
        """node > other: soft edge (không tính vào ready_count).

        Dùng cho output của branch khi chỉ một nhánh được thực thi.
        Ví dụ: case_a > merge_node (merge chờ BẤT KỲ MỘT predecessor)

        LƯU Ý: Python xử lý so sánh chuỗi đặc biệt!
        `a > b > c` được hiểu là `(a > b) and (b > c)`, KHÔNG PHẢI `(a > b) > c`.
        Do đó KHÔNG THỂ viết: `branch > [node1, node2] > merge >> END`
        Phải tách ra:
            branch > [node1, node2] > merge
            merge >> END
        """
        edge_type = "condition" if self.type == "branch" else "normal"
        add_edge = getattr(self.father, "add_edge", None)

        if isinstance(other, list):
            if add_edge is not None:
                for node in other:
                    add_edge(self.name, node.name, edge_type, soft=True)
            return other
        elif getattr(other, "name", None) is not None:
            if add_edge is not None:
                add_edge(self.name, other.name, edge_type, soft=True)
            return other
        return NotImplemented

    def __lt__(self, other):
        """node < other: soft edge ngược.

        Dùng cho output của branch khi chỉ một nhánh được thực thi.
        Ví dụ: merge_node < case_a (merge chờ BẤT KỲ MỘT predecessor)

        Cũng được gọi bởi Python khi: [n1, n2] > self
        (list.__gt__ returns NotImplemented, Python tries self.__lt__(list))
        """
        edge_type = "condition" if self.type == "branch" else "normal"
        add_edge = getattr(self.father, "add_edge", None)

        if isinstance(other, list):
            if add_edge is not None:
                for node in other:
                    add_edge(node.name, self.name, edge_type, soft=True)
            return self  # Return self để chain tiếp: [a, b] > self > next
        elif getattr(other, "name", None) is not None:
            if add_edge is not None:
                add_edge(other.name, self.name, edge_type, soft=True)
            return self
        return NotImplemented

    def __call__(self, **kwargs) -> Dict[str, Any]:
        """Test nhanh: gọi node trực tiếp với inputs.

        Sử dụng:
            node = SomeNode(name="test", ...)
            result = node(a=1, b=2)
            # hoặc
            result = node(**{"a": 1, "b": 2})

        Returns:
            Dict các output từ việc thực thi node.

        Note:
            If this node is a GraphNode that hasn't been built yet,
            it will be automatically built before execution.
        """
        from hush.core.states import MemoryState, StateSchema

        # Auto-build if this is an unbuilt GraphNode
        self._auto_build_if_needed()

        # Tạo schema từ node này
        schema = StateSchema(node=self)
        # Truyền kwargs vào MemoryState để override inputs
        state = MemoryState(schema, inputs=kwargs)

        # Chạy đồng bộ
        try:
            loop = asyncio.get_running_loop()
            # Nếu đã trong async context, chạy trong thread pool
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor() as pool:
                result = pool.submit(asyncio.run, self.run(state)).result()
        except RuntimeError:
            # Không có running loop, dùng asyncio.run()
            result = asyncio.run(self.run(state))

        return result

    def _auto_build_if_needed(self):
        """Auto-build this node if it's a GraphNode that hasn't been built.

        This enables convenient testing: graph() will auto-build before execution.
        """
        # Check if this is a GraphNode (has _is_building attribute)
        if hasattr(self, "_is_building") and self._is_building:
            if hasattr(self, "build"):
                LOGGER.debug("Auto-building graph '%s' before execution", self.name)
                self.build()

    def is_base_node(self) -> bool:
        return True

    def get_inputs(
        self, state: "MemoryState", context_id: str, parent_context: Optional[str] = None
    ) -> Dict[str, Any]:
        """Lấy giá trị input từ state dựa trên ánh xạ kết nối.

        Sử dụng state[this_node, var_name, ctx] để:
        1. Resolve đến vị trí lưu trữ canonical qua schema index
        2. Tự động áp dụng các Ref operation (như ['key'].apply(len))

        Schema đã resolve Ref chain tại thời điểm build, nên ta đọc
        từ tên biến của chính node này - index và ops đã được tính trước.

        Args:
            state: Workflow state
            context_id: Context của node này
            parent_context: Context của PARENT để resolve PARENT refs.
                           Dùng cho iteration nodes khi child cần đọc từ parent.
        """
        result = {}

        for var_name, param in self.inputs.items():
            # Xác định context dựa trên ref target
            # PARENT ref → dùng parent_context (nếu có), sibling/other → dùng context_id
            if (
                parent_context is not None
                and isinstance(param.value, Ref)
                and param.value.raw_node is self.father
            ):
                lookup_ctx = parent_context
            else:
                lookup_ctx = context_id

            # Luôn đọc từ state trước (có thể có giá trị từ MemoryState inputs hoặc Ref)
            value = state[self.full_name, var_name, lookup_ctx]

            if value is not None:
                result[var_name] = value
            elif param.value is not None and not isinstance(param.value, Ref):
                # Fallback: giá trị literal trong Param.value
                result[var_name] = param.value
            elif param.default is not None:
                # Fallback: giá trị default
                result[var_name] = param.default

        return result

    def get_outputs(
        self, state: "MemoryState", context_id: str, parent_context: Optional[str] = None
    ) -> Dict[str, Any]:
        """Lấy giá trị output từ state.

        Đọc trực tiếp từ các biến output của node này. Output connections
        (outputs={...}) đã được schema resolve tại thời điểm build -
        chúng tạo ref ở vị trí đích, không phải ở node này.

        Args:
            state: Workflow state
            context_id: Context của node này
            parent_context: Context của PARENT (để giữ API consistency)
        """
        result = {}
        for var_name in self.outputs:
            result[var_name] = state[self.full_name, var_name, context_id]
        return result

    def store_result(self, state: "MemoryState", result: Dict[str, Any], context_id: str) -> None:
        """Lưu dict kết quả vào state.

        Sử dụng state[node, var, ctx] = value cho lưu trữ O(1) dựa trên index.
        Extracts $tags special key for dynamic tagging.
        """
        if not result:
            return

        # Extract $tags before storing (don't store as output variable)
        tags = result.pop("$tags", None)
        if tags:
            state.add_tags(tags)

        for key, value in result.items():
            state[self.full_name, key, context_id] = value

    def _log(
        self,
        request_id: str,
        context_id: Optional[str],
        inputs: Dict[str, Any],
        outputs: Dict[str, Any],
        duration_ms: float,
    ) -> None:
        """Log tóm tắt thực thi node với inputs, outputs và duration."""
        # Check both verbose flag and logger level before formatting
        if self.verbose and LOGGER.isEnabledFor(20):  # 20 = INFO level
            LOGGER.info(
                "[title]\\[%s][/title] [bold]%s[/bold]: [highlight]%s[/highlight] [muted]\\[%s][/muted] [muted](%.1fms)[/muted] %s -> %s",
                request_id or "unknown",
                self.type.upper() if isinstance(self.type, str) else str(self.type).upper(),
                self.full_name,
                context_id or "main",
                duration_ms,
                format_log_data(inputs),
                format_log_data(outputs),
            )

    async def run(
        self,
        state: "MemoryState",
        context_id: Optional[str] = None,
        parent_context: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Thực thi node.

        Args:
            state: Workflow state
            context_id: Context của node này cho biến của chính nó
            parent_context: Context của PARENT để resolve PARENT refs
        """
        parent_name = self.father.full_name if self.father else None
        state.record_execution(self.full_name, parent_name, context_id)

        # Skip disabled nodes
        if not self.enabled:
            LOGGER.debug("Skipped disabled node: %s", self.full_name)
            return {}

        request_id = state.request_id
        start_time = datetime.now()
        perf_start = perf_counter()
        _inputs = {}
        _outputs = {}

        try:
            _inputs = self.get_inputs(state, context_id, parent_context)

            if asyncio.iscoroutinefunction(self.core):
                _outputs = await self.core(**_inputs)
            else:
                _outputs = self.core(**_inputs)
                # Yield control after synchronous CPU-bound operations
                await asyncio.sleep(0)

            self.store_result(state, _outputs, context_id)

        except Exception:
            error_msg = traceback.format_exc()
            LOGGER.error(
                "[title]\\[%s][/title] Error in node [highlight]%s[/highlight]:\n%s",
                request_id,
                self.name,
                error_msg.rstrip(),
            )
            state[self.full_name, "error", context_id] = error_msg

        finally:
            end_time = datetime.now()
            duration_ms = (perf_counter() - perf_start) * 1000
            self._log(request_id, context_id, _inputs, _outputs, duration_ms)
            state[self.full_name, "start_time", context_id] = start_time
            state[self.full_name, "end_time", context_id] = end_time

            # Performance monitoring: log slow nodes (>100ms)
            if duration_ms > 100:
                LOGGER.warning(
                    "[title]\\[%s][/title] Slow node [highlight]%s[/highlight]: [red]%.1fms[/red]",
                    request_id,
                    self.full_name,
                    duration_ms,
                )

            # Record trace metadata for observability
            state.record_trace_metadata(
                node_name=self.full_name,
                context_id=context_id,
                name=self.name,
                input_vars=list(self.inputs.keys()) if self.inputs else [],
                output_vars=list(self.outputs.keys()) if self.outputs else [],
                parent_name=parent_name,
                start_time=start_time,
                end_time=end_time,
                duration_ms=duration_ms,
                contain_generation=self.contain_generation,
                metadata=self.metadata,
            )
            return _outputs

    def get_input_variables(self) -> List[str]:
        """Trả về danh sách tên biến input."""
        return list(self.inputs.keys()) if self.inputs else []

    def get_output_variables(self) -> List[str]:
        """Trả về danh sách tên biến output."""
        return list(self.outputs.keys()) if self.outputs else []

    @property
    def specific_metadata(self) -> Dict[str, Any]:
        """Trả về metadata riêng của subclass. Override ở các subclass."""
        return {}

    @property
    def metadata(self) -> Dict[str, Any]:
        """Tạo dictionary metadata cho node."""

        def get_connect_key(param: Param):
            if isinstance(param.value, Ref):
                return {param.value.node: param.value.var}
            return param.value

        result = {
            "id": self.id,
            "name": self.full_name,
            "type": self.type,  # Already a lowercase string (Literal type)
        }

        if self.description:
            result["description"] = self.description

        if self.inputs:
            result["input_connects"] = {k: get_connect_key(v) for k, v in self.inputs.items()}

        if self.outputs:
            result["output_connects"] = {k: get_connect_key(v) for k, v in self.outputs.items()}

        if self.sources:
            result["sources"] = f"<- ({','.join(self.sources)})"

        if self.targets:
            result["targets"] = f"-> ({','.join(self.targets)})"

        for flag in ["stream", "start", "end"]:
            if getattr(self, flag, False):
                result[flag] = True

        result.update({k: v for k, v in self.specific_metadata.items() if v})

        return result


class DummyNode(BaseNode):
    """Dummy node cho các marker START/END/PARENT."""

    type: NodeType = "dummy"

    def __init__(self, name: str):
        super().__init__(name=name)

    def __rshift__(self, other):
        """START >> node hoặc node >> END"""
        # Không hỗ trợ soft edge với START/END
        if isinstance(other, SoftEdge):
            raise TypeError(
                f"Không thể dùng soft edge (~) với {self.name}.\n"
                f"  Sai: {self.name} >> ~node\n"
                f"  Đúng: {self.name} >> node"
            )

        if self == START:
            current_graph = get_current()
            if current_graph and hasattr(current_graph, "add_edge"):
                if isinstance(other, list):
                    for node in other:
                        current_graph.add_edge(self.name, node.name)
                    return other
                elif hasattr(other, "name"):
                    current_graph.add_edge(self.name, other.name)
                    return other
        return super().__rshift__(other)

    def __rrshift__(self, other):
        """[nodes] >> START or [nodes] >> END

        Khi node >> END, tự động set outputs = {"*": PARENT} nếu chưa có.
        """
        current_graph = get_current()
        if current_graph and hasattr(current_graph, "add_edge"):
            if self == START:
                if isinstance(other, list):
                    for node in other:
                        current_graph.add_edge(self.name, node.name)
                elif hasattr(other, "name"):
                    current_graph.add_edge(self.name, other.name)
                return self

            elif self == END:
                # Auto-set outputs: mỗi output key -> father[key]
                def _set_wildcard_outputs(node):
                    if hasattr(node, "outputs") and not _has_explicit_outputs(node):
                        if node.outputs is None:
                            node.outputs = {}
                        father = getattr(node, "father", None) or PARENT
                        for key in node.outputs:
                            param = node.outputs[key]
                            if hasattr(param, "value") and param.value is None:
                                param.value = Ref(father, key)

                if isinstance(other, list):
                    for node in other:
                        _set_wildcard_outputs(node)
                        current_graph.add_edge(node.name, self.name)
                elif hasattr(other, "name"):
                    _set_wildcard_outputs(other)
                    current_graph.add_edge(other.name, self.name)
                return self

        return self

    def __rlshift__(self, other):
        """node >> END (deprecated path, __rrshift__ handles this)"""
        if self == END:
            current_graph = get_current()
            if current_graph and hasattr(current_graph, "add_edge"):
                # Auto-set outputs: mỗi output key -> father[key]
                def _set_wildcard_outputs(node):
                    if hasattr(node, "outputs") and not _has_explicit_outputs(node):
                        if node.outputs is None:
                            node.outputs = {}
                        father = getattr(node, "father", None) or PARENT
                        for key in node.outputs:
                            param = node.outputs[key]
                            if hasattr(param, "value") and param.value is None:
                                param.value = Ref(father, key)

                if isinstance(other, list):
                    for node in other:
                        _set_wildcard_outputs(node)
                        current_graph.add_edge(node.name, self.name)
                    return self
                elif hasattr(other, "name"):
                    _set_wildcard_outputs(other)
                    current_graph.add_edge(other.name, self.name)
                    return self
        return self

    def __gt__(self, other):
        """START > node or node > END (soft edge)"""
        if self == START:
            current_graph = get_current()
            if current_graph and hasattr(current_graph, "add_edge"):
                if isinstance(other, list):
                    for node in other:
                        current_graph.add_edge(self.name, node.name, soft=True)
                    return other
                elif hasattr(other, "name"):
                    current_graph.add_edge(self.name, other.name, soft=True)
                    return other
        return super().__gt__(other)

    def __rgt__(self, other):
        """[nodes] > END (soft edge)"""
        current_graph = get_current()
        if current_graph and hasattr(current_graph, "add_edge"):
            if self == END:
                # Auto-set outputs: mỗi output key -> father[key]
                def _set_wildcard_outputs(node):
                    if hasattr(node, "outputs") and not _has_explicit_outputs(node):
                        if node.outputs is None:
                            node.outputs = {}
                        father = getattr(node, "father", None) or PARENT
                        for key in node.outputs:
                            param = node.outputs[key]
                            if hasattr(param, "value") and param.value is None:
                                param.value = Ref(father, key)

                if isinstance(other, list):
                    for node in other:
                        _set_wildcard_outputs(node)
                        current_graph.add_edge(node.name, self.name, soft=True)
                elif hasattr(other, "name"):
                    _set_wildcard_outputs(other)
                    current_graph.add_edge(other.name, self.name, soft=True)
                return self
        return self


# Các dummy node toàn cục
START = DummyNode("__START__")
END = DummyNode("__END__")
PARENT = DummyNode("__PARENT__")
