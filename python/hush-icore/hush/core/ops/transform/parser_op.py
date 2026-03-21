"""ParserOp — extract structured data from text."""

import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional

import yaml

from hush.core.configs.op_config import OpType
from hush.core.exceptions import ParserError
from hush.core.ops.base import BaseOp
from hush.core.utils.common import Param

ParserType = Literal["json", "xml", "yaml"]


@dataclass
class ExtractField:
    """Biểu diễn một field cần trích xuất với path và thông tin type."""

    output_key: str
    chain_path: List[str]
    type_hint: str

    @classmethod
    def from_string(cls, schema_str: str) -> "ExtractField":
        """Parse chuỗi schema như 'company.user.address: dict' thành ExtractField."""
        if ":" not in schema_str:
            schema_str += ": Any"

        chain_text, type_hint = schema_str.split(":", 1)
        chain_path = chain_text.strip().split(".")
        output_key = chain_path[-1]

        return cls(output_key=output_key, chain_path=chain_path, type_hint=type_hint.strip())


def parse_json(text: str) -> Dict[str, Any]:
    """Parse JSON text."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1]) if len(lines) > 2 else text
    return json.loads(text)


def parse_xml(text: str) -> Dict[str, Any]:
    """Parse XML text thành dictionary.

    Handles both single-root and multiple top-level elements.
    For multiple elements like <a>1</a><b>2</b>, wraps in <root> and flattens.
    """

    def xml_to_dict(element):
        result = {}
        for child in element:
            if len(child) == 0:
                result[child.tag] = child.text
            else:
                child_dict = xml_to_dict(child)
                if child.tag in result:
                    if not isinstance(result[child.tag], list):
                        result[child.tag] = [result[child.tag]]
                    result[child.tag].append(child_dict)
                else:
                    result[child.tag] = child_dict
        return result

    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1]) if len(lines) > 2 else text

    # Try parsing as-is first
    try:
        root = ET.fromstring(text)
        return {root.tag: xml_to_dict(root)} if len(root) > 0 else {root.tag: root.text}
    except ET.ParseError:
        # Multiple root elements - wrap in <root> and flatten result
        wrapped = f"<root>{text}</root>"
        root = ET.fromstring(wrapped)
        return xml_to_dict(root)


def parse_yaml(text: str) -> Dict[str, Any]:
    """Parse YAML text."""
    try:
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1]) if len(lines) > 2 else text
        return yaml.safe_load(text)
    except ImportError:
        raise ImportError("pyyaml là bắt buộc để parse YAML")


# Module-level parser lookup for O(1) format selection
_PARSER_MAP = {
    "json": parse_json,
    "xml": parse_xml,
    "yaml": parse_yaml,
}


class ParserOp(BaseOp):
    """Op that parses text into structured data.

    Supports multiple formats (JSON, XML, YAML) and extracts fields using
    dot-separated chain paths (e.g. ``"user.address.city: str"``). Commonly
    used as the final stage inside a ``ChainOp`` pipeline.

    Inputs:
        text (str): Raw text to parse (e.g. LLM output).

    Outputs:
        Dynamically generated from the ``extract`` list — one output key
        per extracted field.

    Example::

        parser = ParserOp(
            format="json",
            extract=["user.name: str", "user.age: int"],
            inputs={"text": llm["content"]},
        )
    """

    type: OpType = "parser"

    __slots__ = ["backend", "format", "extract", "extract_fields", "validators"]

    def __init__(
        self,
        format: Optional[ParserType] = None,
        extract: Optional[List[str]] = None,
        validators: Optional[Dict[str, list]] = None,
        inputs: Dict[str, Any] = None,
        outputs: Dict[str, Any] = None,
        **kwargs,
    ):
        if not extract:
            raise TypeError("extract là bắt buộc")

        # Parse schema thành format có cấu trúc
        extract_fields = [ExtractField.from_string(schema_str) for schema_str in extract]

        # Parse inputs/outputs từ extract
        parsed_inputs = {"text": Param(type=str, required=True)}
        parsed_outputs = {field.output_key: Param() for field in extract_fields}

        # Gọi super().__init__ không truyền inputs/outputs
        super().__init__(**kwargs)

        # Normalize and merge with user-provided
        normalized_inputs = self._normalize_params(inputs)
        normalized_outputs = self._normalize_params(outputs)
        self.inputs = self._merge_params(parsed_inputs, normalized_inputs)
        self.outputs = self._merge_params(parsed_outputs, normalized_outputs)

        self.format = format or "xml"
        self.extract = extract
        self.extract_fields = extract_fields

        # Serialize parser config as literal inputs so Rust can read them
        parser_fmt_param = Param(type=str, required=False)
        parser_fmt_param.value = self.format
        self.inputs["parser_format"] = parser_fmt_param

        parser_ext_param = Param(type=list, required=False)
        parser_ext_param.value = self.extract
        self.inputs["parser_extract"] = parser_ext_param

        self.validators = validators or {}
        self.backend = self._create_parser()
        self.core = self._process

    def _create_parser(self):
        """Tạo parser function dựa trên format."""
        # O(1) lookup for common formats
        parser = _PARSER_MAP.get(self.format)
        if parser is not None:
            return parser

        # Default fallback
        return parse_xml

    def _extract_value_by_path(self, data: Dict[str, Any], chain_path: List[str]) -> Any:
        """Trích xuất giá trị từ nested dictionary theo chain path."""
        current = data
        for key in chain_path:
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                return None
        return current

    def _convert_type(self, value: Any, type_hint: str) -> Any:
        """Convert extracted value to the specified type.

        Handles type conversion for common type hints, especially boolean
        strings from XML parsing ("true"/"false" → True/False).

        Args:
            value: The raw extracted value
            type_hint: Type hint string (e.g., "bool", "int", "str", "float")

        Returns:
            Converted value, or original value if type hint is not recognized
        """
        type_hint = type_hint.lower().strip()

        # Boolean conversion - handles XML string "true"/"false"
        if type_hint in ("bool", "boolean"):
            if value is None:
                return None
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                value_lower = value.lower().strip()
                if value_lower in ("true", "1", "yes"):
                    return True
                if value_lower in ("false", "0", "no", ""):
                    return False
            return bool(value)

        # For non-boolean types, None stays None
        if value is None:
            return None

        # Numeric conversions
        if type_hint == "int":
            try:
                return int(value)
            except (ValueError, TypeError):
                return value

        if type_hint in ("float", "number"):
            try:
                return float(value)
            except (ValueError, TypeError):
                return value

        # String - return as-is or convert to string
        if type_hint in ("str", "string"):
            return str(value) if value is not None else ""

        # For dict, list, any, or unknown types, return as-is
        return value

    async def _process(self, text: str, parser_format=None, parser_extract=None) -> Dict[str, Any]:
        """Parse text và trích xuất các field.

        Returns error as output (not exception) so downstream ops can check it.
        On success: {"field1": value, "field2": value, "error": None}
        On failure: {"field1": None, "field2": None, "error": "error message"}
        """
        if not text:
            return {"error": "Empty input text"}

        try:
            parsed_data = self.backend(text)
        except Exception as e:
            return {"error": f"Parse error ({self.format}): {e}"}

        result = {}
        for field in self.extract_fields:
            raw_value = self._extract_value_by_path(parsed_data, field.chain_path)
            result[field.output_key] = self._convert_type(raw_value, field.type_hint)

        # Validate extracted values against allowed lists
        if self.validators:
            for field_name, allowed_values in self.validators.items():
                value = result.get(field_name)
                if value is None or value not in allowed_values:
                    return {"error": f"Validation failed: '{field_name}' value '{value}' not in {allowed_values}"}

        result["error"] = None
        return result

    @property
    def specific_metadata(self) -> Dict[str, Any]:
        """Trả về metadata riêng của subclass."""
        return {"format": self.format}
