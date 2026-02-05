"""Cấu trúc dữ liệu ánh xạ hai chiều (Bidirectional Map)."""

from typing import TypeVar, Generic, Dict, DefaultDict, Set
from collections import defaultdict

K = TypeVar('K')
V = TypeVar('V')


class BiMapReverse(Generic[V, K]):
    """View ngược cho BiMap, cho phép tra cứu V -> Set[K]."""

    def __init__(self, parent: 'BiMap[K, V]'):
        self._parent = parent

    def __getitem__(self, value: V) -> Set[K]:
        """Tra cứu V -> Set[K]."""
        return self._parent._reverse.get(value, set())

    def __contains__(self, value: V) -> bool:
        return value in self._parent._reverse


class BiMap(Generic[K, V]):
    """Cấu trúc dữ liệu ánh xạ hai chiều.

    Hỗ trợ tra cứu K -> V (forward) và V -> Set[K] (reverse).
    Một value có thể được map bởi nhiều key.

    Example:
        bimap = BiMap()
        bimap["a"] = 1
        bimap["b"] = 1
        bimap["a"]  # -> 1
        bimap.reverse[1]  # -> {"a", "b"}
    """

    def __init__(self):
        self.forward: Dict[K, V] = {}
        self._reverse: DefaultDict[V, Set[K]] = defaultdict(set)
        self._reverse_view = BiMapReverse(self)

    @property
    def reverse(self) -> BiMapReverse[V, K]:
        """Trả về view ngược cho tra cứu V -> Set[K]."""
        return self._reverse_view

    def __getitem__(self, key: K) -> V:
        """Tra cứu K -> V."""
        if key not in self.forward:
            raise KeyError(f"Key '{key}' không tồn tại")
        return self.forward[key]

    def __setitem__(self, key: K, value: V):
        # Xóa mapping cũ nếu tồn tại
        if key in self.forward:
            old_value = self.forward[key]
            self._reverse[old_value].discard(key)
            if not self._reverse[old_value]:
                del self._reverse[old_value]

        # Thiết lập mapping mới
        self.forward[key] = value
        self._reverse[value].add(key)

    def __contains__(self, key: K) -> bool:
        return key in self.forward

    def __repr__(self) -> str:
        forward = dict(self.forward)
        reverse = {v: set(ks) for v, ks in self._reverse.items()}
        return f"BiMap(forward={forward}, reverse={reverse})"
