"""PromptOp — builds chat messages from templates for operon-providers."""

import re
from typing import Any, Dict, List, Union

from operon.core.configs import OpType
from operon.core.exceptions import PromptError
from operon.core.ops import BaseOp
from operon.core.ops.base import shorthand, split_shorthand_kwargs
from operon.core.utils.common import Param

# Reserved keys that are not template variables
RESERVED_KEYS = frozenset(
    [
        "template",
        "conversation_history",
        "tool_results",
    ]
)


class PromptOp(BaseOp):
    """Op that formats a template into chat messages (OpenAI format).

    Accepts three template formats:

    * **str** — becomes a single user message: ``"Hello {name}"``.
    * **dict** — ``{"system": "...", "user": "..."}`` with ``{var}`` placeholders.
    * **list** — full messages array for multimodal / complex prompts.

    All non-reserved input keys are substituted as template variables.

    Inputs:
        template (str | dict | list): Message template. Default: None.
        conversation_history (list): Prior messages to prepend. Default: [].
        tool_results (list): Tool-call results to append. Default: [].
        <var> (any): Template variables (``{var}`` placeholders).

    Outputs:
        messages (list): Formatted chat messages ready for an LLM.

    Example::

        p = PromptOp.of(
            template={"system": "You are {role}.", "user": "{query}"},
            role="helpful",
            query=PARENT["query"],
        )
    """

    type: OpType = "prompt"

    # Fixed input schema - only reserved keys
    INPUT_SCHEMA = {
        "template": Param(type=(str, dict, list), required=False, default=None),
        "conversation_history": Param(type=list, required=False, default=[]),
        "tool_results": Param(type=list, required=False, default=[]),
    }

    # Fixed output schema
    OUTPUT_SCHEMA = {
        "messages": Param(type=list, required=True),
    }

    def __init__(
        self, inputs: Dict[str, Any] = None, outputs: Dict[str, Any] = None, **kwargs: Any
    ):
        """Initialize PromptOp.

        Args:
            inputs: Input mappings. Reserved keys for templates, others are template vars.
            outputs: Output variable mappings
            **kwargs: Additional keyword arguments for BaseOp.
        """
        super().__init__(**kwargs)

        # Copy fixed schema
        parsed_inputs = {
            k: Param(type=v.type, required=v.required, default=v.default)
            for k, v in self.INPUT_SCHEMA.items()
        }
        parsed_outputs = {
            k: Param(type=v.type, required=v.required, default=v.default)
            for k, v in self.OUTPUT_SCHEMA.items()
        }

        # Normalize user inputs
        normalized_inputs = self._normalize_params(inputs)
        normalized_outputs = self._normalize_params(outputs)

        # Wildcard handling: infer template variable names from the source op
        if "__FORWARD_WILDCARD__" in normalized_inputs:
            for var in self._infer_wildcard_vars(normalized_inputs["__FORWARD_WILDCARD__"]):
                if var not in parsed_inputs:
                    parsed_inputs[var] = Param(type=Any, required=False, default=None)

        # Add non-reserved keys from user inputs to schema (template variables)
        for key, param in normalized_inputs.items():
            if key not in RESERVED_KEYS and key != "__FORWARD_WILDCARD__":
                parsed_inputs[key] = Param(type=Any, required=False, default=None)

        self.inputs = self._merge_params(parsed_inputs, normalized_inputs)
        self.outputs = self._merge_params(parsed_outputs, normalized_outputs)

        # Set core function
        self._set_core(self._format)

    def _resolve_wildcard_source(self, wildcard_source):
        """Resolve PARENT sentinel to actual parent op."""
        if hasattr(wildcard_source, "name") and wildcard_source.name == "__PARENT__":
            return self.parent
        return wildcard_source

    def _infer_wildcard_vars(self, wildcard_source) -> set:
        """Infer template variable names from the wildcard source op.

        Strategy:
          1. If source has a static template → parse ``{var}`` placeholders from it.
          2. Otherwise (Ref or missing) → use source's non-reserved input keys.
        """
        from operon.core.states.ref import Ref

        source = self._resolve_wildcard_source(wildcard_source)
        if source is None or not hasattr(source, "inputs"):
            return set()

        # Try static template first
        if "template" in source.inputs:
            param = source.inputs["template"]
            value = param.value if hasattr(param, "value") else param
            if value is not None and not isinstance(value, Ref):
                return self._extract_template_variables(value) - RESERVED_KEYS

        # Fallback: all non-reserved input keys from source
        return set(source.inputs) - RESERVED_KEYS

    def _extract_template_variables(self, template: Any) -> set:
        """Find all ``{variable}`` names in a template (str, dict, or list)."""
        if isinstance(template, str):
            return set(re.findall(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", template))
        if isinstance(template, dict):
            result = set()
            for v in template.values():
                result |= self._extract_template_variables(v)
            return result
        if isinstance(template, list):
            result = set()
            for item in template:
                result |= self._extract_template_variables(item)
            return result
        return set()

    def _format_value(self, value: Any, vars: Dict[str, Any], template: Any = None) -> Any:
        """Recursively format template variables in a value.

        Args:
            value: Value to format
            vars: Template variables
            template: Original template (for error context)
        """
        if isinstance(value, str):
            try:
                return value.format_map(vars) if "{" in value else value
            except KeyError as e:
                # Find all missing variables
                required = set(re.findall(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", value))
                missing = [v for v in required if v not in vars]
                raise PromptError(
                    message="Missing template variable(s)",
                    template=template or value,
                    missing_vars=missing,
                    original_error=e,
                ) from e
        elif isinstance(value, dict):
            return {k: self._format_value(v, vars, template) for k, v in value.items()}
        elif isinstance(value, list):
            return [self._format_value(item, vars, template) for item in value]
        return value

    def _template_to_messages(
        self, template: Union[str, Dict, List], vars: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Convert template to messages array.

        Args:
            template: Template in one of 3 formats (str, dict, list)
            vars: Template variables for formatting

        Returns:
            List of message dicts
        """
        if template is None:
            return []

        # Form 1: String → user message only
        if isinstance(template, str):
            content = self._format_value(template, vars, template)
            return [{"role": "user", "content": content}]

        # Form 2: Dict with system/user keys
        if isinstance(template, dict):
            messages = []

            if "system" in template or "user" in template:
                if "system" in template:
                    content = self._format_value(template["system"], vars, template)
                    messages.append({"role": "system", "content": content})
                if "user" in template:
                    content = self._format_value(template["user"], vars, template)
                    messages.append({"role": "user", "content": content})
                return messages

            # Otherwise treat as a single message dict
            formatted = self._format_value(template, vars, template)
            return [formatted]

        # Form 3: List → full messages array
        if isinstance(template, list):
            return [self._format_value(msg, vars, template) for msg in template]

        raise PromptError(
            message="Invalid template type",
            template=template,
            original_error=TypeError(f"Expected str, dict, or list, got {type(template).__name__}"),
        )

    async def _format(self, **kwargs) -> Dict[str, Any]:
        """Build messages from templates and context.

        Pops reserved keys, uses remaining kwargs as template variables.
        """
        template = kwargs.pop("template", None)
        conversation_history = kwargs.pop("conversation_history", None) or []
        tool_results = kwargs.pop("tool_results", None) or []

        # Remaining kwargs are template variables
        vars = kwargs

        # Build base messages
        messages = self._template_to_messages(template, vars)

        # Insert conversation history before last user message
        if conversation_history:
            last_user_idx = None
            for i in range(len(messages) - 1, -1, -1):
                if messages[i].get("role") == "user":
                    last_user_idx = i
                    break

            if last_user_idx is None:
                messages.extend(conversation_history)
            else:
                messages[last_user_idx:last_user_idx] = conversation_history

        # Add tool results at the end
        if tool_results:
            messages.extend(tool_results)

        return {"messages": messages}

    @shorthand
    def of(cls, template=None, **kwargs) -> "PromptOp":
        """Create a PromptOp with flat kwargs.

        Example::

            p = PromptOp.of(template={"system": "You are {role}.", "user": "{query}"}, role="helpful", query=PARENT["q"])
        """
        input_mappings, init_kwargs = split_shorthand_kwargs(kwargs)
        if template is not None:
            input_mappings["template"] = template
        return cls(inputs=input_mappings or None, **init_kwargs)

    @property
    def specific_metadata(self) -> Dict[str, Any]:
        """Return prompt-specific metadata."""
        metadata = {}

        if "template" in self.inputs:
            val = self.inputs["template"].value
            if val is not None:
                # Convert Ref to string to avoid pickle issues with
                # background trace process (Ref._source can hold GraphOp
                # with unpicklable _contextvars.Token)
                from operon.core.states.ref import Ref

                metadata["template"] = repr(val) if isinstance(val, Ref) else val

        return metadata
