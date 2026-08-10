"""Unified exception classes cho Operon Core nodes.

Cung cấp base exception class với context đầy đủ để debug,
cho phép các node-specific exceptions kế thừa và mở rộng.
"""

from typing import Any, Dict, List, Optional


def _truncate(text: str, max_length: int = 200) -> str:
    """Truncate text và thêm ellipsis nếu vượt quá max_length."""
    if len(text) <= max_length:
        return text
    return text[:max_length] + "..."


class OpError(Exception):
    """Base exception cho tất cả các node errors.

    Chứa context đầy đủ để debug:
    - op_type: Loại node (parser, llm, code, ...)
    - original_error: Exception gốc
    - context: Dict chứa thông tin bổ sung tùy theo node type

    Example:
        raise OpError(
            message="Failed to process",
            op_type="parser",
            original_error=json_error,
            context={"input": text, "format": "json"}
        )
    """

    def __init__(
        self,
        message: str,
        op_type: str,
        original_error: Optional[Exception] = None,
        context: Optional[Dict[str, Any]] = None,
    ):
        self.op_type = op_type
        self.original_error = original_error
        self.context = context or {}

        # Build formatted message
        full_message = self._format_message(message)
        super().__init__(full_message)

    def _format_message(self, message: str) -> str:
        """Format error message với context."""
        parts = [f"[{self.op_type.upper()}] {message}"]

        if self.original_error:
            parts.append(f"  Error: {self.original_error}")

        for key, value in self.context.items():
            if isinstance(value, str):
                value = _truncate(repr(value))
            parts.append(f"  {key}: {value}")

        return "\n".join(parts)


class ParserError(OpError):
    """Exception khi parsing thất bại.

    Context tự động bao gồm:
    - input: Text đầu vào (truncated)
    - format: Loại format (json, xml, yaml)

    Example:
        raise ParserError(
            message="Invalid JSON syntax",
            input_text=raw_text,
            format_type="json",
            original_error=json_decode_error
        )
    """

    def __init__(
        self,
        message: str,
        input_text: str,
        format_type: str,
        original_error: Optional[Exception] = None,
    ):
        self.input_text = input_text
        self.format_type = format_type

        super().__init__(
            message=message,
            op_type="parser",
            original_error=original_error,
            context={"format": format_type, "input": input_text},
        )


class LLMRefusalError(OpError):
    """LLM declined to answer the request.

    Signalled by the provider via ``finish_reason`` (e.g. ``content_filter``,
    ``safety``) or a dedicated ``refusal`` field on the response. Distinct
    from transport failures (which the underlying SDK retries on its own)
    and from semantic failures (parse/validator errors that trigger LLMOp's
    error-guided retry). LLMOp treats a refusal as a signal to try the
    next resource in the ``fallback=`` list rather than retrying the same
    resource.

    Attributes:
        reason: Provider-supplied ``finish_reason`` when known.
        refusal_text: Provider-supplied refusal message (if any).
        resource: The resource key that produced the refusal.
    """

    def __init__(
        self,
        message: str,
        reason: Optional[str] = None,
        refusal_text: Optional[str] = None,
        resource: Optional[str] = None,
        original_error: Optional[Exception] = None,
    ):
        self.reason = reason
        self.refusal_text = refusal_text
        self.resource = resource
        super().__init__(
            message=message,
            op_type="llm",
            original_error=original_error,
            context={
                "reason": reason,
                "refusal_text": refusal_text,
                "resource": resource,
            },
        )


class ValidatorError(OpError):
    """Extracted value failed its declared validator list.

    Raised by the parsing layer inside LLMOp when a field's value is not in
    the ``validators={"field": [...]}`` allow-list AND there is no
    ``@`` -prefixed default in the list. Signals a semantic failure — LLMOp
    routes this to its error-guided retry path, not to ``fallback=``.

    Attributes:
        field: Name of the field that failed validation.
        value: Value that failed the allow-list check.
        allowed: The list of acceptable values.
    """

    def __init__(
        self,
        message: str,
        field: str,
        value: Any,
        allowed: list,
        original_error: Optional[Exception] = None,
    ):
        self.field = field
        self.value = value
        self.allowed = allowed
        super().__init__(
            message=message,
            op_type="llm",
            original_error=original_error,
            context={"field": field, "value": repr(value), "allowed": allowed},
        )


class CodeError(OpError):
    """Exception khi user function trong FuncOp thất bại.

    Context tự động bao gồm:
    - function_name: Tên function
    - source: Source code (truncated)
    - inputs: Dict các input values (truncated)

    Example:
        raise CodeError(
            message="Function raised an exception",
            function_name="calculate_total",
            source="def calculate_total(x, y): ...",
            inputs={"x": 1, "y": 2},
            original_error=division_error
        )
    """

    def __init__(
        self,
        message: str,
        function_name: str,
        source: str,
        inputs: Dict[str, Any],
        original_error: Optional[Exception] = None,
    ):
        self.function_name = function_name
        self.source = source
        self.inputs = inputs

        super().__init__(
            message=message,
            op_type="code",
            original_error=original_error,
            context={
                "function_name": function_name,
                "source": _truncate(source, 300),
                "inputs": {k: _truncate(repr(v), 100) for k, v in inputs.items()},
            },
        )


class BranchError(OpError):
    """Exception khi đánh giá điều kiện branch thất bại.

    Context tự động bao gồm:
    - condition: Biểu thức điều kiện
    - inputs: Dict các input values (truncated)
    - candidates: List các target node có thể

    Example:
        raise BranchError(
            message="Condition evaluation failed",
            condition="score >= 90",
            inputs={"score": "invalid"},
            candidates=["excellent", "good", "fail"],
            original_error=type_error
        )
    """

    def __init__(
        self,
        message: str,
        condition: str,
        inputs: Dict[str, Any],
        candidates: List[str],
        original_error: Optional[Exception] = None,
    ):
        self.condition = condition
        self.inputs = inputs
        self.candidates = candidates

        super().__init__(
            message=message,
            op_type="branch",
            original_error=original_error,
            context={
                "condition": condition,
                "inputs": {k: _truncate(repr(v), 100) for k, v in inputs.items()},
                "candidates": candidates,
            },
        )


# =============================================================================
# Provider Node Errors (operonx-providers)
# =============================================================================


class PromptError(OpError):
    """Exception khi LLMOp format prompt thất bại.

    Context tự động bao gồm:
    - prompt_type: Loại prompt (str/dict/list)
    - prompt: Preview của prompt (truncated)
    - missing_vars: List các biến bị thiếu (nếu có)

    Example:
        raise PromptError(
            message="Missing template variable",
            prompt="Hello {name}, your order {order_id}",
            missing_vars=["order_id"],
            original_error=key_error
        )
    """

    def __init__(
        self,
        message: str,
        prompt: Any,
        missing_vars: Optional[List[str]] = None,
        original_error: Optional[Exception] = None,
    ):
        self.prompt = prompt
        self.missing_vars = missing_vars or []

        context = {
            "prompt_type": type(prompt).__name__,
            "prompt": _truncate(repr(prompt), 300),
        }
        if missing_vars:
            context["missing_vars"] = missing_vars

        super().__init__(
            message=message, op_type="prompt", original_error=original_error, context=context
        )


class EmbeddingError(OpError):
    """Exception khi EmbeddingOp thất bại.

    Context tự động bao gồm:
    - resource: Model/resource key
    - text_count: Số lượng texts cần embed

    Example:
        raise EmbeddingError(
            message="Embedding backend failed",
            resource="bge-m3",
            text_count=100,
            original_error=connection_error
        )
    """

    def __init__(
        self,
        message: str,
        resource: str,
        text_count: int,
        original_error: Optional[Exception] = None,
    ):
        self.resource = resource
        self.text_count = text_count

        super().__init__(
            message=message,
            op_type="embedding",
            original_error=original_error,
            context={"resource": resource, "text_count": text_count},
        )


class RerankError(OpError):
    """Exception khi RerankOp thất bại.

    Context tự động bao gồm:
    - resource: Model/resource key
    - query: Query string (truncated)
    - document_count: Số lượng documents

    Example:
        raise RerankError(
            message="Invalid document type",
            resource="bge-m3",
            query="search query",
            document_count=50,
            original_error=type_error
        )
    """

    def __init__(
        self,
        message: str,
        resource: str,
        query: str,
        document_count: int,
        original_error: Optional[Exception] = None,
    ):
        self.resource = resource
        self.query = query
        self.document_count = document_count

        super().__init__(
            message=message,
            op_type="rerank",
            original_error=original_error,
            context={
                "resource": resource,
                "query": _truncate(query, 100),
                "document_count": document_count,
            },
        )
