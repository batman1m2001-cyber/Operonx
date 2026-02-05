"""Các kiểu config edge cho hush-core."""

from typing import Literal
from pydantic import BaseModel


EdgeType = Literal["normal", "lookback", "condition"]


class EdgeConfig(BaseModel):
    """Config cho các edge giữa các node trong workflow graph.

    Attributes:
        from_node: Tên node nguồn
        to_node: Tên node đích
        type: Loại edge (normal, lookback, condition)
        soft: Nếu True, edge này không được tính vào ready_count.
              Dùng cho các đầu ra nhánh khi chỉ một nhánh được thực thi.
              Được tạo bằng toán tử > thay vì >>
    """

    from_node: str
    to_node: str
    type: EdgeType = "normal"
    soft: bool = False
