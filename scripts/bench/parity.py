"""Bench parity verifier — runs every `<name>.graph.json` + matching
`<name>.inputs.json` through both the Python `Operon` engine and the
Rust binary at `scripts/bench/target/release/operonx-bench`. Prints
PASS/FAIL per pattern with the diffing keys when outputs disagree.

The Python side runs the live `@graph` factory from `generate.py`
(matches what the bench harness measures), then strips the
`$state` / `$collected` keys and compares the resulting dict against
the JSON the Rust binary printed for the same pattern.

Run from the repo root:

    cd scripts/bench && cargo build --release   # one-time
    uv run python scripts/bench/parity.py
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from generate import PATTERNS  # noqa: E402

from operonx.core import Operon  # noqa: E402

RUST_BIN = HERE / "target" / "release" / "operonx-bench"
DATA_DIR = HERE / "data"


def _strip_engine_meta(d: dict) -> dict:
    """Drop `$state` / `$collected` and any other `$`-prefixed engine keys."""
    return {k: v for k, v in d.items() if not (isinstance(k, str) and k.startswith("$"))}


def _normalise(value):
    """Recursively normalise floats/ints for cross-language equality.

    Python's `json` prints `5.0` while Rust may print `5` for the same
    `f64`. Coerce numbers that compare equal as floats to the same
    repr so `==` works.
    """
    if isinstance(value, dict):
        return {k: _normalise(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalise(v) for v in value]
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return float(value)
    return value


async def _run_python(name: str, factory, inputs: dict) -> dict:
    graph = factory()
    engine = Operon(graph)
    result = await engine.run(inputs=inputs)
    return _strip_engine_meta(result)


def _run_rust_all() -> dict[str, dict]:
    """Invoke the Rust bench binary once in `--probe` mode.

    Returns a `{name: result_dict}` mapping by parsing the
    `RESULT <name> <json>` lines the binary emits — one per pattern.
    """
    proc = subprocess.run(
        [str(RUST_BIN), "--probe"],
        capture_output=True,
        text=True,
        check=True,
    )
    out: dict[str, dict] = {}
    for line in proc.stdout.splitlines():
        if not line.startswith("RESULT "):
            continue
        rest = line[len("RESULT ") :]
        name, _, payload = rest.partition(" ")
        out[name] = json.loads(payload)
    if not out:
        raise RuntimeError(
            f"Rust bench `--probe` produced no RESULT lines.\nstdout:\n{proc.stdout}\n"
            f"stderr:\n{proc.stderr}"
        )
    return out


def _diff(a: dict, b: dict) -> list[str]:
    """Return a list of differing top-level keys (with reason)."""
    a, b = _normalise(a), _normalise(b)
    out = []
    for k in sorted(set(a) | set(b)):
        if k not in a:
            out.append(f"{k}: missing on Python side (Rust={b[k]!r})")
        elif k not in b:
            out.append(f"{k}: missing on Rust side (Python={a[k]!r})")
        elif a[k] != b[k]:
            out.append(f"{k}: Python={a[k]!r} Rust={b[k]!r}")
    return out


async def main() -> int:
    if not RUST_BIN.exists():
        print(f"Rust bench binary not built at {RUST_BIN}.", file=sys.stderr)
        print("Run: cd scripts/bench && cargo build --release", file=sys.stderr)
        return 2

    try:
        rust_results = _run_rust_all()
    except Exception as exc:
        print(f"Rust bench probe failed: {exc}", file=sys.stderr)
        return 2

    failed = 0
    for name, factory, inputs in PATTERNS:
        try:
            py_out = await _run_python(name, factory, inputs)
        except Exception as exc:
            print(f"FAIL {name:>26s}  python error: {exc}")
            failed += 1
            continue

        rust_out = rust_results.get(name)
        if rust_out is None:
            print(f"FAIL {name:>26s}  rust did not produce a RESULT line")
            failed += 1
            continue

        diffs = _diff(py_out, rust_out)
        if not diffs:
            keys = ", ".join(sorted(py_out)) or "<empty>"
            print(f"PASS {name:>26s}  ({keys})")
        else:
            failed += 1
            print(f"FAIL {name:>26s}")
            for d in diffs:
                print(f"        {d}")

    if failed == 0:
        print(f"\nAll {len(PATTERNS)} patterns produced identical output on Python ↔ Rust.")
        return 0
    print(f"\n{failed} / {len(PATTERNS)} patterns disagree.")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
