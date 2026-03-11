"""Rust backend bridge — serialize workflow config and spawn Axum binary.

This module handles the `backend="rust"` path for HushApp.serve().
When selected, the Python side serializes graph definitions + endpoint
configs to JSON and spawns a standalone Rust HTTP server (rush-serve)
that executes workflows natively without Python.

Architecture:
    Python (hush-serve)              Rust (rush-serve)
    |- Define graphs via @graph      |- Parse workflow JSON
    |- Register endpoints            |- Load cdylib plugins
    |- Serialize to JSON ----------> |- Build Axum routes
    |- Spawn Rust binary             |- Serve HTTP + SSE + WS
    '- Wait for process              '- Return JSON results

The Rust binary (rush-serve) is a separate crate that:
  - Accepts a config JSON file via --config CLI arg
  - Loads plugin crates referenced by rust= paths in ops
  - Creates Axum routes matching the endpoint definitions
  - Runs entirely without Python — no PyO3 dependency
"""

from __future__ import annotations

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

# Default location of the rush-serve crate relative to hush-serve package
_RUSH_SERVE_CRATE = Path(__file__).resolve().parents[3] / ".." / "rust" / "rush-serve"


class RustBackendConfig:
    """Serializable configuration for the Rust backend."""

    def __init__(self):
        self.endpoints: List[Dict[str, Any]] = []
        self.host: str = "0.0.0.0"
        self.port: int = 8000

    def add_endpoint(self, path: str, graph_config: dict, **kwargs):
        self.endpoints.append({"path": path, "graph": graph_config, **kwargs})

    def to_json(self) -> str:
        return json.dumps(
            {"host": self.host, "port": self.port, "endpoints": self.endpoints},
            default=str,
        )


def serialize_for_rust(app: "HushApp") -> RustBackendConfig:
    """Serialize a HushApp's endpoints into a Rust-compatible config.

    Converts all registered endpoints' graphs via graph.serialize()
    and bundles them with endpoint routing configuration.
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

    return config


def find_rush_serve_binary(crate_dir: Optional[Path] = None) -> Path:
    """Find or build the rush-serve binary.

    Looks for a pre-built binary first, then builds from source if needed.

    Returns:
        Path to the rush-serve binary.

    Raises:
        FileNotFoundError: If the crate directory doesn't exist.
        RuntimeError: If cargo build fails.
    """
    crate = (crate_dir or _RUSH_SERVE_CRATE).resolve()

    if not crate.exists():
        raise FileNotFoundError(
            f"rush-serve crate not found at {crate}. "
            f"Ensure the rush-serve directory exists in the monorepo."
        )

    # Check for pre-built binary
    if sys.platform == "win32":
        binary_name = "rush-serve.exe"
    else:
        binary_name = "rush-serve"

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
    print(f"Building rush-serve from {workspace_root}...")
    result = subprocess.run(
        ["cargo", "build", "--release", "-p", "rush-serve"],
        cwd=str(workspace_root),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to build rush-serve:\n{result.stderr}")
    print("rush-serve built successfully.")

    if not release_bin.exists():
        raise RuntimeError(f"Build succeeded but binary not found at {release_bin}")

    return release_bin


def serve_rust(
    app: "HushApp",
    host: str,
    port: int,
    crate_dir: Optional[Path] = None,
):
    """Spawn the Rust backend server.

    1. Serializes all registered endpoints to JSON config
    2. Writes config to a temp file
    3. Finds or builds the rush-serve binary
    4. Spawns the binary and waits for it

    Args:
        app: The HushApp instance with registered endpoints.
        host: Bind address.
        port: Bind port.
        crate_dir: Optional path to rush-serve crate (auto-detected if None).
    """
    # 1. Serialize config
    config = serialize_for_rust(app)
    config.host = host
    config.port = port
    config_json = config.to_json()

    # 2. Write to temp file
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".json",
        prefix="rush-serve-config-",
        delete=False,
    ) as f:
        f.write(config_json)
        config_path = f.name

    try:
        # 3. Find or build binary
        binary = find_rush_serve_binary(crate_dir)

        # 4. Spawn and wait
        print(f"Starting rush-serve (Rust backend) at http://{host}:{port}")
        print(f"Binary: {binary}")
        print(f"Config: {config_path}")

        proc = subprocess.Popen(
            [str(binary), "--config", config_path],
            stdout=sys.stdout,
            stderr=sys.stderr,
        )

        # Forward SIGINT/SIGTERM to child process
        def _forward_signal(signum, frame):
            proc.send_signal(signum)

        if sys.platform != "win32":
            signal.signal(signal.SIGINT, _forward_signal)
            signal.signal(signal.SIGTERM, _forward_signal)

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
