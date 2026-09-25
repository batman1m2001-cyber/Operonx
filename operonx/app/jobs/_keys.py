"""Is this string a resource key, or a filesystem path?

Three places had to answer that — `as_source`, `as_sink`, and the manifest
loader — and all three answered it the same wrong way::

    if ":" in obj and not Path(obj).exists():
        ...treat it as a resource key

**Every absolute Windows path contains a colon.** `C:\\jobs\\out.jsonl` has
one, and an output file does not exist before the job writes it, so the
string went to the ResourceHub and the job died on `ResourceHub not
initialized` — naming a component the caller never mentioned. Input paths
escaped only because they happen to exist; `as_sink` had no such luck, which
is why the Jobs suite failed on Windows and passed in CI.

A resource key is `category:name`. That is a narrower shape than "has a
colon", and stating it properly costs one regex:

* the category is at least two characters, so a drive letter is not one
* neither half holds a path separator, so nothing under a colonised
  directory is mistaken for a key

The `exists()` check is gone rather than kept as a fallback. A key that
looks like a key should resolve as one whether or not a file of that name
happens to sit in the working directory — otherwise the meaning of a
config string depends on the contents of a folder.
"""

from __future__ import annotations

import re
from typing import Any

#: `category:name` — category at least two chars (so `C:` is not a category),
#: and no path separator on either side (so `./a:b/c.jsonl` is a path).
_RESOURCE_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]+:[^\\/:]+$")

__all__ = ["is_resource_key"]


def is_resource_key(value: Any) -> bool:
    """True when *value* has the shape of a `category:name` resource key.

    Shape only — it does not ask the hub whether the key is registered. The
    caller resolves it and reports its own failure, which keeps a typo in a
    key name distinguishable from a key that was never meant to be one.

    >>> is_resource_key("source:calls_today")
    True
    >>> is_resource_key(r"C:\\jobs\\out.jsonl")
    False
    >>> is_resource_key("/var/data/out.jsonl")
    False
    >>> is_resource_key("out.jsonl")
    False
    """
    return isinstance(value, str) and bool(_RESOURCE_KEY.match(value))
