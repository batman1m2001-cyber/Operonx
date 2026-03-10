"""FuncOp — execute a Python function as a workflow op."""

import ast
import inspect
import textwrap
from functools import wraps
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from hush.core.configs.op_config import OpType
from hush.core.exceptions import CodeError
from hush.core.loggings import LOGGER
from hush.core.ops.base import _BASE_INIT_KEYS, BaseOp, split_shorthand_kwargs
from hush.core.utils.auto_name import register_skip
from hush.core.utils.common import Param

if TYPE_CHECKING:
    from hush.core.states import MemoryState


def op(func=None, *, executor=None, rust=None, bound=None):
    """Decorator that turns a plain function into a FuncOp factory.

    Can be used bare or with keyword arguments::

        @op
        def double(x: int):
            return {"result": x * 2}

        @op(executor="thread")
        def fetch(url: str):
            return {"data": requests.get(url).json()}

        @op(rust="./my_ops::double")
        def double(x: int):
            return {"result": x * 2}  # Python fallback

        @op(bound="io")
        async def call_api(url: str):
            return {"data": await fetch(url)}

        @op(bound="cpu")
        def heavy_compute(data: list):
            return {"result": process(data)}

    Args:
        executor: How to run sync functions. ``None`` (default) runs on
            the event loop, ``"thread"`` uses a thread pool.
        rust: Optional Rust plugin op spec (e.g. ``"./my_ops::double"``).
            Format: ``"<crate_path>::<func_name>"``. The path can be a crate
            directory, a ``.rs`` file, or a pre-built ``.so``/``.dylib``.
            The engine auto-builds the crate via ``cargo build --release``
            if needed. The Python body serves as a fallback.
        bound: Execution bound hint for the scheduler. ``"io"`` for I/O-bound
            ops (HTTP, LLM calls, embeddings) — uses tokio async scheduling.
            ``"cpu"`` for CPU-bound ops (computation) — uses rayon threads.
            ``None`` auto-detects: async → ``"io"``, sync → ``"cpu"``.
    """

    def decorator(fn):
        if rust is not None:
            fn._rust_op_name = rust
        if bound is not None:
            fn._op_bound = bound
        sig = inspect.signature(fn)
        collisions = set(sig.parameters.keys()) & _BASE_INIT_KEYS
        if collisions:
            LOGGER.warning(
                "@op function '%s' has parameter(s) %s that collide with reserved op keywords %s. "
                "When called via shorthand (e.g. %s(name=PARENT['name'])), these may be misinterpreted "
                "as op constructor args instead of function inputs. Consider renaming them.",
                fn.__name__,
                sorted(collisions),
                sorted(_BASE_INIT_KEYS),
                fn.__name__,
            )

        @wraps(fn)
        def wrapper(**kwargs):
            mappings, init_kwargs = split_shorthand_kwargs(kwargs, {"return_keys"})
            # Call-time overrides decoration-time defaults
            op_executor = init_kwargs.pop("executor", executor)
            op_bound = init_kwargs.pop("bound", bound)
            return FuncOp(
                code_fn=fn,
                executor=op_executor,
                bound=op_bound,
                _mappings=mappings or None,
                **init_kwargs,
            )

        register_skip(wrapper)
        wrapper.__wrapped__ = fn
        return wrapper

    if func is not None:
        # @op without parentheses
        return decorator(func)
    # @op(executor="thread") with parentheses
    return decorator


TYPE_MAP = {
    "str": str,
    "string": str,
    "int": int,
    "integer": int,
    "float": float,
    "bool": bool,
    "boolean": bool,
    "list": list,
    "List": list,
    "dict": dict,
    "Dict": dict,
    "any": Any,
    "Any": Any,
}


def parse_default_value(value_str: str, param_type: type) -> Any:
    """Parse chuỗi giá trị mặc định thành type phù hợp.

    Trả về None nếu parse thất bại.
    """
    value_str = value_str.strip()

    # Xử lý chuỗi rỗng
    if not value_str:
        if param_type == list:
            return []
        elif param_type == dict:
            return {}
        elif param_type == str:
            return ""
        return None

    try:
        if param_type == bool:
            return value_str.lower() in ("true", "1", "yes")
        elif param_type == int:
            return int(value_str)
        elif param_type == float:
            return float(value_str)
        elif param_type == list:
            return []
        elif param_type == dict:
            return {}
        else:
            return value_str  # str hoặc Any
    except (ValueError, TypeError):
        return None


def parse_comment(comment: str) -> tuple:
    """Parse comment dạng '(type) description' hoặc '(type=default) description'.

    Chỉ nhận diện các type đã biết trong ngoặc đơn ở ĐẦU comment.

    Ví dụ:
        '(str) greeting message' -> (str, None, 'greeting message')
        '(int=0) the count' -> (int, 0, 'the count')
        '(bool=true) enabled flag' -> (bool, True, 'enabled flag')
        'just a description' -> (Any, None, 'just a description')
        'use (carefully) here' -> (Any, None, 'use (carefully) here')
    """
    comment = comment.strip()
    default = None

    if comment.startswith("(") and ")" in comment:
        close_idx = comment.index(")")
        type_part = comment[1:close_idx].strip()

        # Kiểm tra giá trị mặc định: (type=default)
        if "=" in type_part:
            type_str, default_str = type_part.split("=", 1)
            type_str = type_str.strip()
        else:
            type_str = type_part
            default_str = None

        # Chỉ coi là type nếu thuộc danh sách type đã biết
        if type_str in TYPE_MAP:
            param_type = TYPE_MAP[type_str]
            description = comment[close_idx + 1 :].strip()

            # Parse giá trị mặc định nếu có
            if default_str is not None:
                default = parse_default_value(default_str, param_type)

            return param_type, default, description

    # Không tìm thấy type hợp lệ, coi toàn bộ comment là description
    return Any, None, comment


def _build_comment_map(source_lines: List[str]) -> Dict[str, str]:
    """Build {key_name: comment_str} from source lines in one pass.

    Scans for lines containing a quoted string key followed by #comment.
    """
    import re

    comment_map = {}
    pattern = re.compile(r"""['"](\w+)['"]\s*:.*#\s*(.+)""")
    for line in source_lines:
        m = pattern.search(line)
        if m:
            comment_map[m.group(1)] = m.group(2).strip()
    return comment_map


def _extract_dict_keys(dict_node: ast.Dict, comment_map: Dict[str, str]) -> Dict[str, Param]:
    """Extract keys from an AST Dict node, using pre-built comment map for O(1) lookup."""
    schema = {}
    for key_node in dict_node.keys:
        if isinstance(key_node, ast.Constant) and isinstance(key_node.value, str):
            key_name = key_node.value
            comment = comment_map.get(key_name)
            if comment:
                param_type, default, description = parse_comment(comment)
            else:
                param_type, default, description = Any, None, ""
            schema[key_name] = Param(type=param_type, default=default, description=description)
    return schema


def extract_return_schema(func: Callable) -> Dict[str, Param]:
    """Extract return schema from function source code using AST.

    Supports return {"key": value}, lambda x: {"key": value},
    and yield {"key": value} (streaming ops). Inline comments
    after keys are parsed for type hints and descriptions.
    """
    try:
        source = inspect.getsource(func)
        source_lines = source.splitlines()
        cleaned_source = textwrap.dedent(source)

        # Try to parse the source directly
        tree = None
        try:
            tree = ast.parse(cleaned_source)
        except SyntaxError:
            # Lambda in function call on single line — extract with brace matching
            lambda_idx = cleaned_source.find("lambda")
            if lambda_idx != -1:
                brace_start = cleaned_source.find("{", lambda_idx)
                if brace_start != -1:
                    depth = 1
                    pos = brace_start + 1
                    while pos < len(cleaned_source) and depth > 0:
                        if cleaned_source[pos] == "{":
                            depth += 1
                        elif cleaned_source[pos] == "}":
                            depth -= 1
                        pos += 1
                    if depth == 0:
                        try:
                            tree = ast.parse(cleaned_source[lambda_idx:pos])
                        except SyntaxError:
                            pass

        if tree is None:
            return {}

        # Build comment map once, reuse for all dict nodes
        comment_map = _build_comment_map(source_lines)

        schema = {}
        for node in ast.walk(tree):
            dict_value = None
            if isinstance(node, ast.Return) and node.value and isinstance(node.value, ast.Dict):
                dict_value = node.value
            elif isinstance(node, ast.Lambda) and isinstance(node.body, ast.Dict):
                dict_value = node.body
            elif isinstance(node, ast.Yield) and node.value and isinstance(node.value, ast.Dict):
                dict_value = node.value

            if dict_value is not None:
                schema.update(_extract_dict_keys(dict_value, comment_map))

        return schema
    except Exception:
        LOGGER.debug("Failed to extract return schema for %s", getattr(func, "__name__", func))
        return {}


class FuncOp(BaseOp):
    """Op that executes a Python function.

    Inputs and outputs are auto-extracted from the function's signature and
    return-statement AST. Both sync and async functions are supported.
    Prefer the ``@op`` decorator over instantiating ``FuncOp`` directly.

    Inputs:
        Auto-parsed from the function's parameter list.

    Outputs:
        Auto-parsed from ``return {"key": ...}`` via AST, or from
        explicit ``return_keys``.

    Example::

        @op
        def add(a: int, b: int):
            return {"sum": a + b}

        with GraphOp(name="main") as graph:
            result = add(a=PARENT["x"], b=PARENT["y"])
            START >> result >> END
    """

    type: OpType = "code"

    __slots__ = ["code_fn", "source"]

    def __init__(
        self,
        code_fn: Optional[Callable] = None,
        return_keys: Optional[List[str]] = None,
        inputs: Dict[str, Any] = None,
        outputs: Dict[str, Any] = None,
        _mappings: Dict[str, Any] = None,
        **kwargs,
    ):
        # Parse inputs/outputs từ function signature/AST
        parsed_inputs, parsed_outputs = self._parse_function(code_fn, return_keys)

        # Split _mappings into inputs/outputs using parsed schema
        if _mappings:
            for key, value in _mappings.items():
                if key in parsed_inputs:
                    if inputs is None:
                        inputs = {}
                    inputs[key] = value
                elif key in parsed_outputs:
                    if outputs is None:
                        outputs = {}
                    outputs[key] = value
                else:
                    raise TypeError(
                        f"'{key}' is not a known input or output of {code_fn.__name__}(). "
                        f"Inputs: {set(parsed_inputs)}, Outputs: {set(parsed_outputs)}"
                    )

        # Gọi super().__init__ không truyền inputs/outputs
        super().__init__(**kwargs)

        # Normalize user-provided inputs/outputs first (handles {"*": PARENT} wildcard)
        normalized_inputs = self._normalize_params(inputs)
        normalized_outputs = self._normalize_params(outputs)

        # Merge parsed schema với normalized inputs/outputs
        self.inputs = self._merge_params(parsed_inputs, normalized_inputs)
        self.outputs = self._merge_params(parsed_outputs, normalized_outputs)

        self.code_fn = code_fn
        self.core = code_fn

        # Lấy source code
        try:
            self.source = inspect.getsource(code_fn) if code_fn else ""
        except:
            self.source = str(code_fn) if code_fn else ""

        # Set description từ docstring nếu chưa có
        if not self.description and code_fn and code_fn.__doc__:
            self.description = code_fn.__doc__.strip().split("\n")[0]

    def _parse_function(
        self, code_fn: Optional[Callable], return_keys: Optional[List[str]]
    ) -> tuple:
        """Parse inputs/outputs từ function signature và source.

        Returns:
            Tuple[Dict[str, Param], Dict[str, Param]]: (inputs, outputs)
        """
        if code_fn is None:
            return {}, {}

        # Parse inputs từ function parameters
        inputs = {}
        sig = inspect.signature(code_fn)

        for param_name, param in sig.parameters.items():
            param_type = param.annotation if param.annotation != inspect.Parameter.empty else None
            has_default = param.default != inspect.Parameter.empty
            default_val = param.default if has_default else None

            inputs[param_name] = Param(
                type=param_type, required=not has_default, default=default_val
            )

        # Parse outputs: return_keys explicit > AST parsing
        if return_keys:
            outputs = {key: Param() for key in return_keys}
        else:
            # Parse return schema từ source code (với type hints và descriptions)
            outputs = extract_return_schema(code_fn)

        return inputs, outputs

    async def run(
        self,
        state: "MemoryState",
        context_id: Optional[str] = None,
        parent_context: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Thực thi FuncOp với error wrapping.

        Override để wrap exceptions trong CodeError với context đầy đủ.
        """
        try:
            return await super().run(state, context_id, parent_context)
        except CodeError:
            raise  # Đã wrapped, không wrap lại
        except Exception as e:
            # Lấy inputs để có context cho error
            _inputs = self.get_inputs(state, context_id, parent_context)
            raise CodeError(
                message=f"Function '{self.code_fn.__name__ if self.code_fn else 'unknown'}' raised an exception",
                function_name=self.code_fn.__name__ if self.code_fn else "unknown",
                source=self.source,
                inputs=_inputs,
                original_error=e,
            ) from e

    @property
    def specific_metadata(self) -> Dict[str, Any]:
        """Trả về metadata riêng của subclass."""
        return {
            "code_fn": self.source[:200] + "..." if len(self.source) > 200 else self.source,
            "function_name": self.code_fn.__name__ if self.code_fn else "unknown",
        }
