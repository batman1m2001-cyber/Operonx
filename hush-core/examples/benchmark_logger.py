#!/usr/bin/env python3
"""Benchmark logging performance across different configurations.

Compares:
1. Plain text logging (use_rich=False)
2. Rich logging with full format_log_data
3. Rich logging with simple/collapsed format
"""

import time
import logging
import io
import sys
from contextlib import redirect_stdout, redirect_stderr

from hush.core.loggings import LogConfig, setup_logger, format_log_data


# Suppress output during benchmark
class NullWriter:
    def write(self, _): pass
    def flush(self): pass
    def buffer(self): return self
    def reconfigure(self, **_): pass


def format_log_data_simple(data, max_items=3):
    """Simple collapsed format - minimal processing."""
    if data is None:
        return "None"
    if isinstance(data, dict):
        if not data:
            return "{}"
        if len(data) <= max_items:
            return "{" + ", ".join(f"{k}=..." for k in data.keys()) + "}"
        return f"<dict {len(data)} keys>"
    if isinstance(data, (list, tuple)):
        return f"<{type(data).__name__} {len(data)}>"
    if isinstance(data, str):
        return f"'{data[:30]}...'" if len(data) > 30 else f"'{data}'"
    return str(data)


def benchmark_plain_text(iterations: int, test_data: dict):
    """Benchmark plain text logging (with markup stripping)."""
    config = LogConfig(
        name='bench.plain',
        level='INFO',
        handlers=[
            {'type': 'console', 'level': 'INFO', 'use_rich': False},
        ]
    )
    logger = setup_logger(config)

    # Redirect output to null
    old_stdout, old_stderr = sys.stdout, sys.stderr
    sys.stdout = sys.stderr = NullWriter()

    try:
        start = time.perf_counter()
        for i in range(iterations):
            # Include markup tags - will be stripped by PlainTextFormatter
            logger.info("[title]\\[req-%s][/title] Processing with data %s", i, format_log_data(test_data))
        elapsed = time.perf_counter() - start
    finally:
        sys.stdout, sys.stderr = old_stdout, old_stderr

    return elapsed


def benchmark_rich_full(iterations: int, test_data: dict):
    """Benchmark Rich logging with full format_log_data."""
    config = LogConfig(
        name='bench.rich_full',
        level='INFO',
        handlers=[
            {'type': 'console', 'level': 'INFO', 'use_rich': True},
        ]
    )
    logger = setup_logger(config)

    old_stdout, old_stderr = sys.stdout, sys.stderr
    sys.stdout = sys.stderr = NullWriter()

    try:
        start = time.perf_counter()
        for i in range(iterations):
            logger.info("[title]\\[req-%s][/title] Processing with data %s", i, format_log_data(test_data))
        elapsed = time.perf_counter() - start
    finally:
        sys.stdout, sys.stderr = old_stdout, old_stderr

    return elapsed


def benchmark_rich_simple(iterations: int, test_data: dict):
    """Benchmark Rich logging with simple collapsed format."""
    config = LogConfig(
        name='bench.rich_simple',
        level='INFO',
        handlers=[
            {'type': 'console', 'level': 'INFO', 'use_rich': True},
        ]
    )
    logger = setup_logger(config)

    old_stdout, old_stderr = sys.stdout, sys.stderr
    sys.stdout = sys.stderr = NullWriter()

    try:
        start = time.perf_counter()
        for i in range(iterations):
            logger.info("[title]\\[req-%s][/title] Processing with data %s", i, format_log_data_simple(test_data))
        elapsed = time.perf_counter() - start
    finally:
        sys.stdout, sys.stderr = old_stdout, old_stderr

    return elapsed


def benchmark_format_only(iterations: int, test_data: dict):
    """Benchmark format_log_data function alone (no logging)."""
    start = time.perf_counter()
    for _ in range(iterations):
        format_log_data(test_data)
    elapsed = time.perf_counter() - start
    return elapsed


def benchmark_format_simple_only(iterations: int, test_data: dict):
    """Benchmark simple format function alone (no logging)."""
    start = time.perf_counter()
    for _ in range(iterations):
        format_log_data_simple(test_data)
    elapsed = time.perf_counter() - start
    return elapsed


def run_benchmarks():
    """Run all benchmarks and print results."""
    iterations = 10000

    # Test data scenarios
    test_cases = {
        "small_dict": {"name": "John", "age": 30, "active": True},
        "medium_dict": {
            "name": "John Doe",
            "age": 30,
            "email": "john@example.com",
            "active": True,
            "score": 95.5,
            "tags": ["admin", "user"],
        },
        "large_dict": {
            f"key_{i}": f"value_{i}" if i % 3 == 0 else i
            for i in range(20)
        },
        "nested_dict": {
            "user": {"id": 1, "name": "test", "roles": ["admin"]},
            "config": {"timeout": 30, "retries": 3},
            "data": list(range(100)),
        },
        "long_string": {"content": "A" * 500, "meta": "short"},
    }

    print("=" * 70)
    print("LOGGING BENCHMARK")
    print(f"Iterations: {iterations:,}")
    print("=" * 70)

    for case_name, test_data in test_cases.items():
        print(f"\n--- {case_name} ---")
        print(f"Data: {str(test_data)[:60]}...")
        print()

        # Format functions only
        t_format_full = benchmark_format_only(iterations, test_data)
        t_format_simple = benchmark_format_simple_only(iterations, test_data)

        print(f"  format_log_data (full):     {t_format_full*1000:8.2f}ms  ({iterations/t_format_full:,.0f} ops/s)")
        print(f"  format_log_data (simple):   {t_format_simple*1000:8.2f}ms  ({iterations/t_format_simple:,.0f} ops/s)")
        print(f"  Format speedup:             {t_format_full/t_format_simple:.2f}x")
        print()

        # Full logging
        t_plain = benchmark_plain_text(iterations, test_data)
        t_rich_full = benchmark_rich_full(iterations, test_data)
        t_rich_simple = benchmark_rich_simple(iterations, test_data)

        print(f"  Plain text logging:         {t_plain*1000:8.2f}ms  ({iterations/t_plain:,.0f} ops/s)")
        print(f"  Rich + full format:         {t_rich_full*1000:8.2f}ms  ({iterations/t_rich_full:,.0f} ops/s)")
        print(f"  Rich + simple format:       {t_rich_simple*1000:8.2f}ms  ({iterations/t_rich_simple:,.0f} ops/s)")
        print()
        print(f"  Plain vs Rich+full:         {t_rich_full/t_plain:.2f}x slower")
        print(f"  Rich+simple vs Rich+full:   {t_rich_full/t_rich_simple:.2f}x faster")

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print("""
Tips for best performance:
1. Use plain text (use_rich=False) for high-throughput scenarios
2. Use simple format for large/nested data structures
3. Avoid logging in hot paths - use DEBUG level and filter
4. Consider async logging for production workloads
""")


if __name__ == "__main__":
    run_benchmarks()
