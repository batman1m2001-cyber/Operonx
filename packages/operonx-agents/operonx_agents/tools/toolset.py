"""``Toolset`` — the tools one agent owns, and the only ones it can run.

Dispatch resolves a model's call against the toolset it was given and
nothing else. A tool another agent owns, in the same process, answers
"no tool named ..." exactly like a tool that does not exist.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Union

from operonx_agents.tools.tool import Tool, tool

__all__ = ["Toolset"]


class Toolset:
    """An ordered set of :class:`Tool`\\ s with unique names.

    A plain function is accepted and made a tool with ``@tool``'s
    defaults, so ``Toolset([lookup_order, refund])`` works whether or not
    the functions were decorated. A toolset (an ``MCPToolset``, say) is
    accepted as its tools.

    Raises:
        ValueError: on two tools with one name — the model could not say
            which it meant, and dispatch would run whichever came last.
    """

    __slots__ = ("_tools", "_definitions")

    def __init__(self, tools: Iterable[Union[Tool, "Toolset", Callable[..., Any]]] = ()) -> None:
        self._tools: Dict[str, Tool] = {}
        for t in _flat(tools):
            if t.name in self._tools:
                raise ValueError(
                    f"Toolset has two tools named {t.name!r}. The model calls a tool by name, so "
                    "rename one: @tool(name=...), or MCPToolset(prefix=...)."
                )
            self._tools[t.name] = t
        self._definitions = [t.spec.definition() for t in self._tools.values()]

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    @property
    def names(self) -> List[str]:
        return list(self._tools)

    def definitions(self) -> List[Dict[str, Any]]:
        """Every tool as a ``tools=[...]`` entry, in order (a new list of
        the same entries each call: built once, sent every turn)."""
        return list(self._definitions)

    def __iter__(self) -> Iterator[Tool]:
        return iter(self._tools.values())

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.names})"


def _flat(items: Iterable[Any]) -> Iterator[Tool]:
    for item in items:
        if isinstance(item, Toolset):
            yield from item
        else:
            yield item if isinstance(item, Tool) else tool(item)
