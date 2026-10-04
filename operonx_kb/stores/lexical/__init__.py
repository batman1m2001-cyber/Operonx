"""The lexical index (PLAN R1): the contract, SQLite FTS5 and Postgres FTS."""

from operonx_kb.stores.lexical.base import LexicalIndex
from operonx_kb.stores.lexical.sqlite import SqliteLexicalIndex

__all__ = ["LexicalIndex", "SqliteLexicalIndex"]
