"""Workflow state với Cell-based storage và độ phân giải O(1) dựa trên index."""

from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING
import uuid

from hush.core.states.schema import StateSchema
from hush.core.states.cell import Cell, DEFAULT_CONTEXT

if TYPE_CHECKING:
    from hush.core.tracers.store import TraceStore

__all__ = ["MemoryState"]

_uuid4 = uuid.uuid4


class MemoryState:
    """Workflow state với Cell-based storage và truy cập O(1) theo index.

    Thiết kế đơn giản:
        - Read pulls (1 hop): Nếu có input ref, lấy từ source và cache
        - Write pushes (1 hop): Nếu có output ref, đẩy đến target
        - Không có recursion, không có magic

    Luồng dữ liệu:
        __setitem__: Lưu value, nếu output ref thì push 1 hop
        __getitem__: Nếu cached trả về, nếu input ref thì pull 1 hop và cache

    Tracing:
        When trace_store is set, trace data is written directly to SQLite
        instead of being stored in memory. This reduces memory usage for
        large workflows and provides crash resilience.
    """

    __slots__ = (
        "schema", "_cells", "_execution_order", "_trace_metadata",
        "_user_id", "_session_id", "_request_id",
        "_trace_store", "_execution_count", "_tags"
    )

    def __init__(
        self,
        schema: StateSchema,
        inputs: Dict[str, Any] = None,
        user_id: str = None,
        session_id: str = None,
        request_id: str = None,
        trace_store: Optional["TraceStore"] = None,
    ) -> None:
        """Khởi tạo MemoryState.

        Args:
            schema: StateSchema định nghĩa cấu trúc state
            inputs: Giá trị input ban đầu cho workflow
            user_id: ID người dùng (tự động tạo nếu không cung cấp)
            session_id: ID phiên (tự động tạo nếu không cung cấp)
            request_id: ID yêu cầu (tự động tạo nếu không cung cấp)
            trace_store: Optional TraceStore for incremental trace writes
        """
        self.schema = schema
        self._cells: List[Cell] = [Cell(v) for v in schema._defaults]
        self._user_id = user_id or str(_uuid4())
        self._session_id = session_id or str(_uuid4())
        self._request_id = request_id or str(_uuid4())

        # Dynamic tags collected during execution
        self._tags: List[str] = []

        # Tracing - either memory or SQLite
        self._trace_store = trace_store
        self._execution_count = 0

        if trace_store is None:
            # Legacy mode: store in memory
            self._execution_order: List[Dict[str, str]] = []
            self._trace_metadata: Dict[str, Dict[str, Any]] = {}
        else:
            # New mode: write to SQLite, don't keep in memory
            self._execution_order = None
            self._trace_metadata = None

        # Áp dụng input ban đầu
        if inputs:
            for var, value in inputs.items():
                idx = schema.get_index(schema.name, var)
                if idx >= 0:
                    self._cells[idx][None] = value

    # =========================================================================
    # Core API: Simple and predictable
    # =========================================================================

    def __setitem__(self, key: Tuple[str, str, Optional[str]], value: Any) -> None:
        """Store value. Push to target if push_ref exists (1 hop only).

        Args:
            key: Tuple (node, var, ctx)
            value: Giá trị cần lưu
        """
        node, var, ctx = key
        idx = self.schema.get_index(node, var)
        if idx < 0:
            raise KeyError(f"({node}, {var}) không có trong schema")

        ctx_key = ctx if ctx is not None else DEFAULT_CONTEXT
        self._cells[idx][ctx_key] = value

        # Push ref? Push 1 hop to target
        push_ref = self.schema._push_refs[idx]
        if push_ref and push_ref.idx >= 0:
            self._cells[push_ref.idx][ctx_key] = push_ref._fn(value)

    def __getitem__(self, key: Tuple[str, str, Optional[str]]) -> Any:
        """Get value. Pull from source if pull_ref exists (1 hop only).

        Args:
            key: Tuple (node, var, ctx)

        Returns:
            Giá trị tại (node, var, ctx) hoặc None nếu không tìm thấy
        """
        node, var, ctx = key
        idx = self.schema.get_index(node, var)
        if idx < 0:
            return None

        ctx_key = ctx if ctx is not None else DEFAULT_CONTEXT
        cell = self._cells[idx]

        # Has cached value? Return it
        if ctx_key in cell:
            return cell[ctx_key]

        # Pull ref? Pull 1 hop from source and cache
        pull_ref = self.schema._pull_refs[idx]
        if pull_ref and not pull_ref.is_output and pull_ref.idx >= 0:
            source_cell = self._cells[pull_ref.idx]
            # Check both context value and default_value (for static values)
            if ctx_key in source_cell or source_cell.default_value is not None:
                result = pull_ref._fn(source_cell[ctx_key])
                cell[ctx_key] = result  # Cache
                return result

        # No value - return default
        return cell.default_value

    def get(self, node: str, var: str, ctx: Optional[str] = None) -> Any:
        """Lấy giá trị với tham số explicit."""
        return self[node, var, ctx]

    def get_cell(self, node: str, var: str) -> Cell:
        """Lấy object Cell cho một biến."""
        idx = self.schema.get_index(node, var)
        if idx < 0:
            raise KeyError(f"({node}, {var}) không có trong schema")
        return self._cells[idx]

    def has(self, node: str, var: str, ctx: Optional[str] = None) -> bool:
        """Check if value exists (without resolving ref)."""
        idx = self.schema.get_index(node, var)
        if idx < 0:
            return False
        ctx_key = ctx if ctx is not None else DEFAULT_CONTEXT
        return ctx_key in self._cells[idx]

    # =========================================================================
    # Index-based Access (O(1)) - Raw access without ref resolution
    # =========================================================================

    def get_by_index(self, idx: int, ctx: Optional[str] = None) -> Any:
        """Truy cập cell trực tiếp theo index (không resolve ref)."""
        if 0 <= idx < len(self._cells):
            return self._cells[idx][ctx]
        raise IndexError(f"Index {idx} ngoài phạm vi")

    def set_by_index(self, idx: int, value: Any, ctx: Optional[str] = None) -> None:
        """Gán giá trị cell trực tiếp theo index (không push ref)."""
        if 0 <= idx < len(self._cells):
            self._cells[idx][ctx] = value
        else:
            raise IndexError(f"Index {idx} ngoài phạm vi")

    # =========================================================================
    # Execution Tracking
    # =========================================================================

    def record_execution(self, node_name: str, parent: str, context_id: str) -> None:
        """Ghi lại thực thi node cho observability.

        In legacy mode (no trace_store), appends to _execution_order list.
        With trace_store, this is a no-op as tracing happens in record_trace_metadata.
        """
        if self._execution_order is not None:
            self._execution_order.append({
                "node": node_name,
                "parent": parent,
                "context_id": context_id
            })

    def record_trace_metadata(
        self,
        node_name: str,
        context_id: Optional[str],
        name: str,
        input_vars: List[str],
        output_vars: List[str],
        node_type: Optional[str] = None,
        parent_name: Optional[str] = None,
        start_time: Any = None,
        end_time: Any = None,
        duration_ms: Optional[float] = None,
        contain_generation: bool = False,
        model: Optional[str] = None,
        usage: Optional[Dict[str, int]] = None,
        cost: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Store trace metadata for a node execution.

        With trace_store: writes directly to SQLite (incremental).
        Without trace_store: stores in memory (legacy mode).

        Args:
            node_name: Full name of the node
            context_id: Context ID for the execution
            name: Display name for the trace
            input_vars: List of input variable names
            output_vars: List of output variable names
            parent_name: Parent node name (for SQLite mode)
            start_time: Start time (datetime object)
            end_time: End time (datetime object)
            duration_ms: Execution duration in milliseconds
            contain_generation: Whether this node contains LLM generation
            model: Model name (for LLM nodes)
            usage: Token usage dict with prompt_tokens/completion_tokens/total_tokens
            cost: Cost in USD (for LLM nodes)
            metadata: Additional metadata dict
        """
        if self._trace_store is not None:
            # New mode: write directly to SQLite
            # Build input/output data from cells
            input_data = {}
            for var in input_vars:
                input_data[var] = self.get(node_name, var, context_id)

            output_data = {}
            for var in output_vars:
                output_data[var] = self.get(node_name, var, context_id)

            # Extract token counts from usage dict
            prompt_tokens = None
            completion_tokens = None
            total_tokens = None
            if usage:
                prompt_tokens = usage.get("prompt_tokens")
                completion_tokens = usage.get("completion_tokens")
                total_tokens = usage.get("total_tokens")

            # Format times as ISO strings
            start_time_str = start_time.isoformat() if start_time else None
            end_time_str = end_time.isoformat() if end_time else None

            self._trace_store.insert_node_trace(
                request_id=self._request_id,
                workflow_name=self.schema.name,
                node_name=node_name,
                parent_name=parent_name,
                context_id=context_id,
                execution_order=self._execution_count,
                start_time=start_time_str,
                end_time=end_time_str,
                duration_ms=duration_ms,
                input_data=input_data,
                output_data=output_data,
                user_id=self._user_id,
                session_id=self._session_id,
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                cost_usd=cost,
                contain_generation=contain_generation,
                metadata=metadata,
            )
            self._execution_count += 1
        else:
            # Legacy mode: store in memory
            key = f"{node_name}:{context_id}" if context_id else node_name
            self._trace_metadata[key] = {
                "name": name,
                "input_vars": input_vars,
                "output_vars": output_vars,
                "contain_generation": contain_generation,
                "model": model,
                "usage": usage,
                "cost": cost,
                "metadata": metadata or {},
            }

    # =========================================================================
    # Properties
    # =========================================================================

    @property
    def name(self) -> str:
        """Tên của workflow."""
        return self.schema.name

    @property
    def execution_order(self) -> List[Dict[str, str]]:
        """Danh sách thứ tự thực thi các node (legacy mode only)."""
        if self._execution_order is None:
            return []
        return self._execution_order.copy()

    @property
    def trace_metadata(self) -> Dict[str, Dict[str, Any]]:
        """Get all trace metadata for observability (legacy mode only)."""
        if self._trace_metadata is None:
            return {}
        return self._trace_metadata.copy()

    @property
    def metadata(self) -> Dict[str, Any]:
        """Metadata của state bao gồm user_id, session_id, request_id."""
        return {
            "user_id": self._user_id,
            "session_id": self._session_id,
            "request_id": self._request_id,
        }

    @property
    def user_id(self) -> str:
        """ID người dùng."""
        return self._user_id

    @property
    def session_id(self) -> str:
        """ID phiên."""
        return self._session_id

    @property
    def request_id(self) -> str:
        """ID yêu cầu."""
        return self._request_id

    @property
    def has_trace_store(self) -> bool:
        """Whether this state uses SQLite trace store."""
        return self._trace_store is not None

    @property
    def tags(self) -> List[str]:
        """Dynamic tags collected during execution."""
        return self._tags.copy()

    def add_tag(self, tag: str) -> None:
        """Add a dynamic tag to this execution.

        Tags are used for filtering/grouping traces in observability tools.
        Duplicate tags are ignored.

        Args:
            tag: Tag string to add (e.g., "error", "cache-hit", "fallback")
        """
        if tag not in self._tags:
            self._tags.append(tag)

    def add_tags(self, tags: List[str]) -> None:
        """Add multiple dynamic tags to this execution.

        Args:
            tags: List of tag strings to add
        """
        for tag in tags:
            self.add_tag(tag)

    # =========================================================================
    # Collection Interface
    # =========================================================================

    def __contains__(self, key: Tuple[str, str]) -> bool:
        """Kiểm tra (node, var) có tồn tại trong schema không."""
        return key in self.schema

    def __len__(self) -> int:
        """Số lượng cell."""
        return len(self._cells)

    def __iter__(self):
        """Duyệt qua các cặp (node, var)."""
        return iter(self.schema)

    # =========================================================================
    # Context Manager và Tiện ích
    # =========================================================================

    def __enter__(self) -> "MemoryState":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name='{self.name}', cells={len(self._cells)})"

    def __hash__(self) -> int:
        return hash(self._request_id)

    def __eq__(self, other) -> bool:
        if not isinstance(other, MemoryState):
            return False
        return self._request_id == other._request_id

    def show(self) -> None:
        """Hiển thị debug các giá trị state hiện tại."""
        print(f"\n=== {self.__class__.__name__}: {self.name} ===")

        for node, var in self.schema:
            idx = self.schema.get_index(node, var)
            cell = self._cells[idx]
            pull_ref = self.schema._pull_refs[idx]

            if not cell.contexts:
                # Chưa có giá trị
                if pull_ref:
                    print(f"{node}.{var} -> pull_ref[{pull_ref.idx}] (chưa có giá trị)")
                else:
                    print(f"{node}.{var} -> {cell.default_value}")
            elif len(cell.contexts) == 1:
                # Một context
                ctx = next(iter(cell.contexts))
                value = cell.contexts[ctx]
                value_str = repr(value)[:50] + "..." if len(repr(value)) > 50 else repr(value)
                print(f"{node}.{var} [{ctx}] = {value_str}")
            else:
                # Nhiều context
                print(f"{node}.{var}:")
                for ctx, value in cell.contexts.items():
                    value_str = repr(value)[:50] + "..." if len(repr(value)) > 50 else repr(value)
                    print(f"  [{ctx}] = {value_str}")