"""TritonOp — generic Triton Inference Server gRPC client op.

Wraps tritonclient.grpc for any Triton model. Stateless per call.
Config via ResourceHub (resources.yaml).

Example::

    stt = TritonOp(
        name="stt",
        resource="stt",
        inputs_map={"AUDIO_SIGNAL": "audio"},
        outputs_map={"TRANSCRIPT": "transcript", "EMBEDDING": "embedding"},
        inputs={"audio": speech["speech_audio"]},
    )
"""

import logging
from typing import Any, Dict, Optional

import numpy as np

from hush.core.configs import OpType
from hush.core.ops import BaseOp
from hush.core.ops.base import shorthand, split_shorthand_kwargs
from hush.core.utils.common import Param

LOGGER = logging.getLogger(__name__)

# Lazy import tritonclient
_aio_grpcclient = None


def _get_aio_grpcclient():
    global _aio_grpcclient
    if _aio_grpcclient is None:
        try:
            import tritonclient.grpc.aio as aio_grpc

            _aio_grpcclient = aio_grpc
        except ImportError:
            raise ImportError(
                "tritonclient is required for TritonOp. "
                "Install with: pip install tritonclient[grpc]"
            )
    return _aio_grpcclient


# Cache async Triton clients per URL
_triton_clients: Dict[str, Any] = {}


def _get_triton_client(url: str):
    """Get or create an async Triton gRPC client for a given URL."""
    if url not in _triton_clients:
        aio_grpc = _get_aio_grpcclient()
        _triton_clients[url] = aio_grpc.InferenceServerClient(url=url)
    return _triton_clients[url]


# Numpy dtype → Triton dtype string
_DTYPE_MAP = {
    np.float32: "FP32",
    np.float64: "FP64",
    np.float16: "FP16",
    np.int32: "INT32",
    np.int64: "INT64",
    np.int16: "INT16",
    np.int8: "INT8",
    np.uint8: "UINT8",
    np.bool_: "BOOL",
}


def _numpy_to_triton_dtype(arr: np.ndarray) -> str:
    """Map numpy dtype to Triton dtype string."""
    for np_dtype, triton_str in _DTYPE_MAP.items():
        if arr.dtype == np_dtype:
            return triton_str
    raise ValueError(f"Unsupported numpy dtype: {arr.dtype}")


class TritonOp(BaseOp):
    """Generic Triton Inference Server gRPC op.

    Inputs/outputs are dynamically mapped via inputs_map/outputs_map.

    Args:
        resource: Key in resources.yaml["triton"] section.
            Must contain: url, model.
            Optional: model_version, timeout.
        inputs_map: {triton_input_name: op_input_name}
            Maps Triton model input names to op input variable names.
        outputs_map: {triton_output_name: op_output_name}
            Maps Triton model output names to op output variable names.

    Example resources.yaml::

        triton:
          stt:
            url: localhost:8001
            model: fastconformer_asr
          tts:
            url: localhost:3001
            model: tts_tritonv2
    """

    __slots__ = [
        "resource",
        "url",
        "model_name",
        "model_version",
        "timeout",
        "inputs_map",
        "outputs_map",
        "_client",
    ]

    type: OpType = "triton"

    def __init__(
        self,
        resource=None,
        inputs_map: Optional[Dict[str, str]] = None,
        outputs_map: Optional[Dict[str, str]] = None,
        inputs: Optional[Dict[str, Any]] = None,
        outputs: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ):
        """
        Args:
            resource: str (hub lookup "triton:name") or dict (inline config).
                String: looks up "triton:{resource}" in ResourceHub.
                Dict: {"url": "...", "model": "...", "model_version": "", "timeout": 30}
        """
        kwargs.setdefault("bound", "io")
        super().__init__(**kwargs)

        self.inputs_map = inputs_map or {}
        self.outputs_map = outputs_map or {}

        # Store resource for lazy resolution at run time
        if isinstance(resource, str):
            self.resource = resource
            self.url = None
            self.model_name = None
            self.model_version = ""
            self.timeout = 30.0
        elif isinstance(resource, dict):
            self.resource = None
            self.url = resource.get("url")
            self.model_name = resource.get("model")
            self.model_version = resource.get("model_version", "")
            self.timeout = resource.get("timeout", 30.0)
            # Merge maps from config if not provided explicitly
            if not self.inputs_map:
                self.inputs_map = resource.get("inputs_map", {})
            if not self.outputs_map:
                self.outputs_map = resource.get("outputs_map", {})
        else:
            raise TypeError("TritonOp requires resource (str for hub lookup or dict for inline config)")

        # Build I/O schema from maps
        input_schema = {op_name: Param(required=True) for op_name in self.inputs_map.values()}
        output_schema = {op_name: Param() for op_name in self.outputs_map.values()}

        self.inputs = self._merge_params(input_schema, self._normalize_params(inputs))
        self.outputs = self._merge_params(output_schema, self._normalize_params(outputs))

        # Lazy client init
        self._client = None

        self._set_core(self._process)

    def _resolve_resource(self, resource: str) -> dict:
        """Resolve triton resource config from ResourceHub."""
        try:
            from hush.core.registry import ResourceHub

            hub = ResourceHub.instance()
            key = f"triton:{resource}" if ":" not in resource else resource
            result = hub.get_config(key)
            if hasattr(result, "model_dump"):
                return result.model_dump()
            elif isinstance(result, dict):
                return result
            return {}
        except Exception as e:
            LOGGER.warning("Failed to resolve triton resource '%s': %s", resource, e)
            return {}

    def _get_client(self):
        if self._client is None:
            self._client = _get_triton_client(self.url)
        return self._client

    async def _process(self, **kwargs) -> Dict[str, Any]:
        """Run Triton inference.

        Accepts op input names (from inputs_map values),
        maps to Triton input names, calls inference, maps outputs back.
        """
        # Lazy resolve resource on first run
        if self.url is None and self.resource:
            config = self._resolve_resource(self.resource)
            self.url = config.get("url")
            self.model_name = config.get("model")
            self.model_version = config.get("model_version", "")
            self.timeout = config.get("timeout", 30.0)
            if not self.inputs_map:
                self.inputs_map = config.get("inputs_map", {})
            if not self.outputs_map:
                self.outputs_map = config.get("outputs_map", {})
            if not self.url or not self.model_name:
                raise ValueError(f"TritonOp resource '{self.resource}' resolved to invalid config: {config}")

        aio_grpc = _get_aio_grpcclient()
        client = self._get_client()

        # Build Triton inputs
        triton_inputs = []
        for triton_name, op_name in self.inputs_map.items():
            data = kwargs.get(op_name)
            if data is None:
                continue

            # Convert to numpy if needed
            if not isinstance(data, np.ndarray):
                data = np.array(data)

            # Ensure at least 1D
            if data.ndim == 0:
                data = data.reshape(1)

            triton_dtype = _numpy_to_triton_dtype(data)
            inp = aio_grpc.InferInput(triton_name, list(data.shape), triton_dtype)
            inp.set_data_from_numpy(data)
            triton_inputs.append(inp)

        # Build Triton outputs
        triton_outputs = [
            aio_grpc.InferRequestedOutput(triton_name) for triton_name in self.outputs_map.keys()
        ]

        # Async inference — enables Triton dynamic batching
        try:
            result = await client.infer(
                model_name=self.model_name,
                model_version=self.model_version,
                inputs=triton_inputs,
                outputs=triton_outputs,
                client_timeout=self.timeout,
            )
        except Exception as e:
            LOGGER.error("Triton inference failed for model '%s': %s", self.model_name, e)
            raise

        # Map outputs
        output = {}
        for triton_name, op_name in self.outputs_map.items():
            try:
                raw = result.as_numpy(triton_name)
                # Decode bytes/string outputs
                if raw.dtype.kind in ("S", "U", "O"):
                    # Byte string or object → decode to str
                    if raw.ndim == 0:
                        output[op_name] = (
                            raw.item().decode("utf-8")
                            if isinstance(raw.item(), bytes)
                            else str(raw.item())
                        )
                    else:
                        decoded = [
                            v.decode("utf-8") if isinstance(v, bytes) else str(v) for v in raw.flat
                        ]
                        output[op_name] = decoded[0] if len(decoded) == 1 else decoded
                else:
                    output[op_name] = raw
            except Exception as e:
                LOGGER.warning("Failed to get output '%s' from Triton: %s", triton_name, e)
                output[op_name] = None

        return output

    @shorthand
    def of(cls, resource=None, **kwargs) -> "TritonOp":
        """Shorthand factory.

        Example::

            stt = TritonOp.of(
                resource="stt",
                inputs_map={"AUDIO_SIGNAL": "audio"},
                outputs_map={"TRANSCRIPT": "transcript"},
                audio=speech["speech_audio"],
            )
        """
        input_mappings, init_kwargs = split_shorthand_kwargs(kwargs)
        return cls(resource=resource, inputs=input_mappings or None, **init_kwargs)

    def serialize(self) -> dict:
        base = super().serialize()
        base["resource"] = self.resource
        base["url"] = self.url
        base["model_name"] = self.model_name
        base["inputs_map"] = self.inputs_map
        base["outputs_map"] = self.outputs_map
        return base
