"""Rust backend bridge — serialize workflow config and spawn Axum binary.

This module handles the `backend="rust"` path for HushApp.serve().
When selected, the Python side serializes graph definitions + endpoint
configs to JSON and spawns a standalone Rust HTTP server (hush-serve)
that executes workflows natively without Python.

Architecture:
    Python (hush-serve)              Rust (hush-serve)
    |- Define graphs via @graph      |- Parse workflow JSON
    |- Register endpoints            |- Load cdylib plugins
    |- Serialize to JSON ----------> |- Build Axum routes
    |- Spawn Rust binary             |- Serve HTTP + SSE + WS
    '- Wait for process              '- Return JSON results

The Rust binary (hush-serve) is a separate crate that:
  - Accepts a config JSON file via --config CLI arg
  - Loads cdylib plugin via --plugin CLI arg
  - Creates Axum routes matching the endpoint definitions
  - Runs entirely without Python — no PyO3 dependency
"""

from __future__ import annotations

import atexit
import json
import os
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from hush.serve.app import HushApp

# Default location of the hush-serve crate relative to hush-serve package
_HUSH_SERVE_CRATE = Path(__file__).resolve().parents[3] / ".." / "rust" / "hush-serve"


class RustBackendConfig:
    """Serializable configuration for the Rust backend."""

    def __init__(self):
        self.endpoints: List[Dict[str, Any]] = []
        self.host: str = "0.0.0.0"
        self.port: int = 8000
        self.tracers: Optional[Dict[str, Any]] = None

    def add_endpoint(self, path: str, graph_config: dict, **kwargs):
        self.endpoints.append({"path": path, "graph": graph_config, **kwargs})

    def to_json(self) -> str:
        data: Dict[str, Any] = {
            "host": self.host,
            "port": self.port,
            "endpoints": self.endpoints,
        }
        if self.tracers:
            data["tracers"] = self.tracers
        return json.dumps(data, default=str)


def serialize_for_rust(app: "HushApp") -> RustBackendConfig:
    """Serialize a HushApp's endpoints into a Rust-compatible config.

    Converts all registered endpoints' graphs via graph.serialize()
    and bundles them with endpoint routing configuration.
    Also extracts tracer configurations for the Rust backend.
    """
    config = RustBackendConfig()

    for ep in app._endpoints:
        graph_config = ep.graph.serialize()
        config.add_endpoint(
            path=ep.config.path,
            graph_config=graph_config,
            stream=ep.config.stream,
            websocket=ep.config.websocket,
        )

    # Extract tracer configs from app-level tracer
    config.tracers = _extract_tracer_configs(app._tracer)

    return config


def _extract_tracer_configs(tracer) -> Optional[Dict[str, Any]]:
    """Extract Rust-compatible tracer configs from Python Tracer instances."""
    if tracer is None:
        return None

    tracers = tracer if isinstance(tracer, list) else [tracer]
    result: Dict[str, Any] = {}

    for t in tracers:
        cls_name = type(t).__name__

        if cls_name == "LangfuseTracer" and hasattr(t, "_config") and t._config is not None:
            cfg = t._config
            result["langfuse"] = {
                "public_key": cfg.public_key,
                "secret_key": cfg.secret_key,
                "host": cfg.host,
            }
            if hasattr(t, "_stream_trace_limit") and t._stream_trace_limit is not None:
                result["langfuse"]["stream_trace_limit"] = t._stream_trace_limit

        elif cls_name == "HushEyesTracer" and hasattr(t, "_host"):
            result["hush_eyes"] = {
                "host": getattr(t, "_host", "127.0.0.1"),
                "port": getattr(t, "_port", 8420),
            }

    return result if result else None


def find_hush_serve_binary(crate_dir: Optional[Path] = None) -> Path:
    """Find or build the hush-serve binary.

    Search order:
    1. Pre-built binary in crate_dir (monorepo development)
    2. hush-serve on PATH (installed via ``cargo install hush-serve``)
    3. Build from source if crate_dir exists

    Returns:
        Path to the hush-serve binary.

    Raises:
        FileNotFoundError: If hush-serve cannot be found anywhere.
        RuntimeError: If cargo build fails.
    """
    import shutil

    crate = (crate_dir or _HUSH_SERVE_CRATE).resolve()

    if not crate.exists():
        # Crate dir not found — check PATH (e.g. installed via cargo install)
        on_path = shutil.which("hush-serve")
        if on_path:
            return Path(on_path)
        raise FileNotFoundError(
            "hush-serve binary not found. Either:\n"
            "  - Install it: cargo install hush-serve\n"
            "  - Or ensure the hush-serve crate exists in the monorepo"
        )

    # Check for pre-built binary
    if sys.platform == "win32":
        binary_name = "hush-serve.exe"
    else:
        binary_name = "hush-serve"

    # Cargo workspace puts binaries in the workspace root target dir
    workspace_target = crate.parent / "target"
    release_bin = workspace_target / "release" / binary_name
    debug_bin = workspace_target / "debug" / binary_name

    # Prefer release build if it exists
    if release_bin.exists():
        return release_bin
    if debug_bin.exists():
        return debug_bin

    # Build from source (run from workspace root)
    workspace_root = crate.parent
    print(f"Building hush-serve from {workspace_root}...")
    result = subprocess.run(
        ["cargo", "build", "--release", "-p", "hush-serve"],
        cwd=str(workspace_root),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to build hush-serve:\n{result.stderr}")
    print("hush-serve built successfully.")

    if not release_bin.exists():
        raise RuntimeError(f"Build succeeded but binary not found at {release_bin}")

    return release_bin


def _attach_to_job(proc: subprocess.Popen):
    """Attach child process to a Windows Job Object that kills on close.

    When the parent Python process exits (for any reason), Windows automatically
    terminates all processes assigned to this job object.
    """
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32

    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.POINTER(ctypes.c_ulong)),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", ctypes.c_byte * 48),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE

    # 9 = JobObjectExtendedLimitInformation
    kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
    kernel32.AssignProcessToJobObject(job, int(proc._handle))


def _find_cdylib(rust_ops_path: Path) -> Optional[Path]:
    """Find a built cdylib in a Rust ops crate's target directory."""
    rust_ops_path = rust_ops_path.resolve()
    target_dir = rust_ops_path / "target"

    if sys.platform == "win32":
        ext = ".dll"
    elif sys.platform == "darwin":
        ext = ".dylib"
    else:
        ext = ".so"

    for profile in ("release", "debug"):
        profile_dir = target_dir / profile
        if profile_dir.exists():
            for f in profile_dir.iterdir():
                if f.suffix == ext and f.is_file():
                    return f

    return None


def _build_cdylib(rust_ops_path: Path) -> Path:
    """Build a cdylib from a Rust ops crate and return the path to the built library."""
    rust_ops_path = rust_ops_path.resolve()

    print(f"Building cdylib plugin from {rust_ops_path}...")
    result = subprocess.run(
        ["cargo", "build", "--release"],
        cwd=str(rust_ops_path),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to build cdylib plugin:\n{result.stderr}")
    print("Plugin built successfully.")

    cdylib = _find_cdylib(rust_ops_path)
    if cdylib is None:
        raise RuntimeError(f"Build succeeded but no cdylib found in {rust_ops_path / 'target'}")
    return cdylib


def serve_rust(
    app: "HushApp",
    host: str,
    port: int,
    crate_dir: Optional[Path] = None,
    rust_ops: Optional[str] = None,
    plugin: Optional[str] = None,
):
    """Spawn the Rust backend server.

    1. Serializes all registered endpoints to JSON config
    2. Writes config to a temp file
    3. Finds or builds the hush-serve binary
    4. Optionally builds and loads a cdylib plugin
    5. Spawns the binary and waits for it

    Args:
        app: The HushApp instance with registered endpoints.
        host: Bind address.
        port: Bind port.
        crate_dir: Optional path to hush-serve crate (auto-detected if None).
        rust_ops: Optional path to a Rust ops crate (auto-builds cdylib).
        plugin: Optional path to a pre-built cdylib plugin (.dll/.so/.dylib).
    """
    # Resolve plugin path
    plugin_path: Optional[str] = None
    if plugin:
        plugin_path = str(Path(plugin).resolve())
    elif rust_ops:
        ops_path = Path(rust_ops)
        cdylib = _find_cdylib(ops_path)
        if cdylib is None:
            cdylib = _build_cdylib(ops_path)
        plugin_path = str(cdylib)

    # 1. Serialize config
    config = serialize_for_rust(app)
    config.host = host
    config.port = port
    config_json = config.to_json()

    # 2. Write to temp file
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".json",
        prefix="hush-serve-config-",
        delete=False,
    ) as f:
        f.write(config_json)
        config_path = f.name

    try:
        # 3. Find or build binary
        binary = find_hush_serve_binary(crate_dir)

        # 4. Spawn and wait
        print(f"Starting hush-serve (Rust backend) at http://{host}:{port}")
        print(f"Binary: {binary}")
        print(f"Config: {config_path}")
        if plugin_path:
            print(f"Plugin: {plugin_path}")

        cmd = [str(binary), "--config", config_path]
        if plugin_path:
            cmd.extend(["--plugin", plugin_path])

        proc = subprocess.Popen(
            cmd,
            stdout=sys.stdout,
            stderr=sys.stderr,
        )

        # Ensure child is killed when parent exits
        if sys.platform == "win32":
            _attach_to_job(proc)
        else:
            signal.signal(signal.SIGINT, lambda s, f: proc.send_signal(s))
            signal.signal(signal.SIGTERM, lambda s, f: proc.send_signal(s))

        atexit.register(proc.terminate)

        try:
            proc.wait()
        except KeyboardInterrupt:
            proc.terminate()
            proc.wait(timeout=5)

    finally:
        # Clean up temp config file
        try:
            os.unlink(config_path)
        except OSError:
            pass
