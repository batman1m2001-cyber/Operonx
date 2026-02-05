"""Node branch cho định tuyến có điều kiện trong workflow."""

from typing import Dict, Any, Optional, List, Union, Tuple, TYPE_CHECKING

from hush.core.configs.node_config import NodeType
from hush.core.nodes.base import BaseNode
from hush.core.states.ref import Ref
from hush.core.utils.common import Param
from hush.core.loggings import LOGGER

if TYPE_CHECKING:
    from hush.core.states import MemoryState


class BranchNode(BaseNode):
    """Node đánh giá các điều kiện và định tuyến luồng đến các node đích khác nhau.

    Hỗ trợ anchor để override định tuyến.
    """

    type: NodeType = "branch"

    __slots__ = [
        'given_candidates',
        'default',
        'cases',
    ]

    def __init__(
        self,
        cases: Optional[List[Tuple[Ref, str]]] = None,
        candidates: Optional[List[str]] = None,
        default: Optional[str] = None,
        inputs: Dict[str, Any] = None,
        outputs: Dict[str, Any] = None,
        **kwargs
    ):
        # Parse inputs/outputs từ cases
        parsed_inputs, parsed_outputs = self._parse_cases(cases or [])

        # Gọi super().__init__ không truyền inputs/outputs
        super().__init__(**kwargs)

        # Merge parsed với user-provided
        self.inputs = self._merge_params(parsed_inputs, inputs)
        self.outputs = self._merge_params(parsed_outputs, outputs)

        self.default = default.name if hasattr(default, 'is_base_node') else default
        self.given_candidates = candidates
        self.cases = cases or []

        self.core = self._create_core_function()

    def _parse_cases(
        self,
        cases: List[Tuple[Ref, str]]
    ) -> tuple:
        """Parse inputs/outputs từ cases.

        Args:
            cases: List of (condition_ref, target) tuples

        Returns:
            Tuple[Dict[str, Param], Dict[str, Param]]: (inputs, outputs)
        """
        # Inputs: anchor + các biến trong điều kiện
        inputs = {"anchor": Param(type=str, default=None)}

        for ref, target in cases:
            # Use get_all_vars() to extract all variables from compound refs
            # e.g., (PARENT["a"] > 10) & (PARENT["b"] == "x") -> {"a", "b"}
            for var_name in ref.get_all_vars():
                if var_name not in inputs:
                    # Create base Ref without ops for input resolution
                    base_ref = Ref(ref.raw_node, var_name)
                    inputs[var_name] = Param(required=True, value=base_ref)

        # Outputs
        outputs = {
            "target": Param(type=str, required=True),
            "matched": Param(type=str)
        }

        return inputs, outputs

    @property
    def candidates(self) -> List[str]:
        """Danh sách các node đích có thể."""
        if self.given_candidates:
            return self.given_candidates

        targets = [target for _, target in self.cases]

        if self.default:
            return targets + [self.default]
        else:
            return targets

    def _create_core_function(self):
        """Tạo function đánh giá core đã tối ưu."""
        def core(**inputs) -> Dict[str, str]:
            anchor = inputs.get('anchor')
            if anchor:
                return {"target": anchor, "matched": "anchor"}

            target, matched = self._evaluate_conditions(inputs)
            return {"target": target, "matched": matched}

        return core

    def _evaluate_conditions(self, inputs: Dict[str, Any]) -> tuple:
        """Đánh giá tất cả điều kiện và trả về điều kiện khớp đầu tiên."""
        safe_inputs = dict(inputs)

        for ref, target in self.cases:
            try:
                value = safe_inputs.get(ref.var)
                # Pass context for compound boolean operations (& and |)
                result = ref.execute(value, context=safe_inputs)

                if result:
                    condition_desc = f"ref:{ref.var}"
                    LOGGER.debug("Điều kiện [str]'%s'[/str] khớp, định tuyến đến [highlight]%s[/highlight]", condition_desc, target)
                    return target, condition_desc

            except Exception as e:
                LOGGER.error("Lỗi khi đánh giá condition cho [str]'%s'[/str]: %s", ref.var, e)
                continue

        if self.default:
            LOGGER.debug("Không có điều kiện khớp, sử dụng target mặc định [highlight]%s[/highlight]", self.default)
            return self.default, "default"
        else:
            LOGGER.warning("Không có điều kiện khớp và không có target mặc định")
            return None, None

    def get_target(
        self,
        state: 'MemoryState',
        context_id: Optional[str] = None
    ) -> Optional[str]:
        """Lấy target đã định tuyến từ state."""
        return state[self.full_name, "target", context_id]

    def specific_metadata(self) -> Dict[str, Any]:
        """Trả về metadata riêng của subclass."""
        return {
            "cases": [(str(ref), target) for ref, target in self.cases],
            "default_target": self.default,
            "candidates": self.candidates,
            "num_conditions": len(self.cases)
        }


class Branch:
    """Fluent builder để tạo BranchNode.

    Ví dụ:
        # Auto name from variable
        router = Branch().if_(PARENT["score"] >= 90, "excellent").else_("fail")

        # Explicit name
        router = Branch("score_router").if_(PARENT["score"] >= 90, "excellent").else_("fail")

        # Hoặc dùng if_() shorthand
        router = if_(PARENT["score"] >= 90, "excellent").else_("fail")
    """

    __slots__ = ('_name', '_cases', '_default', '_inputs', '_kwargs')
    _is_hush_builder = True

    def __init__(self, name: Optional[str] = None, **kwargs):
        """Khởi tạo Branch builder.

        Args:
            name: Tên của BranchNode. Nếu None, tự suy ra từ tên biến.
            **kwargs: Các tham số khác cho BranchNode
        """
        self._name = name
        self._cases: List[Tuple[Ref, str]] = []
        self._default: Optional[str] = None
        self._inputs: Dict[str, Any] = {}
        self._kwargs = kwargs

    def if_(self, condition: Ref, target: Union[str, BaseNode]) -> 'Branch':
        """Thêm một case với điều kiện từ Ref.

        Args:
            condition: Ref với comparison operation (e.g., PARENT["score"] >= 90)
            target: Node đích hoặc tên node đích

        Returns:
            self để chain tiếp

        Example:
            .if_(PARENT["score"] >= 90, "excellent")
            .if_(PARENT["score"] >= 70, good_node)
        """
        target_name = target.name if hasattr(target, 'name') else target
        self._cases.append((condition, target_name))
        return self

    def else_(self, target: Union[str, BaseNode]) -> 'BranchNode':
        """Đặt default target và build BranchNode.

        Args:
            target: Node đích mặc định khi không có điều kiện nào khớp

        Returns:
            BranchNode đã được tạo
        """
        _skip_auto_name = True  # noqa: F841
        self._default = target.name if hasattr(target, 'name') else target
        return self._build()

    def build(self) -> 'BranchNode':
        """Build BranchNode (dùng khi không có default).

        Returns:
            BranchNode đã được tạo
        """
        _skip_auto_name = True  # noqa: F841
        return self._build()

    def _build(self) -> 'BranchNode':
        """Internal build method."""
        _skip_auto_name = True  # noqa: F841

        all_inputs = {}

        for condition_ref, target in self._cases:
            var_name = condition_ref.var
            if var_name not in all_inputs:
                base_ref = Ref(condition_ref.raw_node, var_name)
                all_inputs[var_name] = base_ref

        return BranchNode(
            name=self._name,
            cases=self._cases,
            default=self._default,
            inputs=all_inputs,
            **self._kwargs
        )


def if_(condition: Ref, target: Union[str, BaseNode]) -> Branch:
    """Start a branch declaration.

    Ví dụ:
        router = if_(PARENT["score"] >= 90, "excellent").else_("fail")

        router = (if_(PARENT["score"] >= 90, "excellent")
                  .if_(PARENT["score"] >= 70, "good")
                  .else_("fail"))
    """
    return Branch().if_(condition, target)
