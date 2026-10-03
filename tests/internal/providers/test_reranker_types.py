"""Every ``RerankingType`` a config accepts has a backend behind it.

``RerankingType.COHERE`` validated in ``resources.yaml`` and then failed at
first use with ``Unsupported Model`` — the factory never had a branch for
it. A type the config accepts is a promise the factory must keep, so the
enum and the factory are checked against each other here.

No backend is constructed for real: each backend module is swapped for a
stand-in, so the check needs no model files, no network and no torch.
"""

import sys
from types import ModuleType

import pytest
from pydantic import ValidationError

from operonx.providers.rerankers.config import RerankingConfig, RerankingType
from operonx.providers.rerankers.factory import create_reranking

pytestmark = pytest.mark.unit

#: backend module (under operonx.providers.rerankers) -> the class the factory imports
_BACKENDS = {
    "tei": "TEIReranker",
    "vllm": "VLLMReranker",
    "pinecone": "PineconeReranker",
    "huggingface": "HFReranker",
    "onnx": "ONNXReranker",
}


@pytest.fixture
def stand_in_backends(monkeypatch):
    """Replace each backend module with one whose class records its config."""
    for module, cls_name in _BACKENDS.items():
        fake = ModuleType(f"operonx.providers.rerankers.{module}")
        setattr(fake, cls_name, type(cls_name, (), {"__init__": lambda self, c: None}))
        monkeypatch.setitem(sys.modules, fake.__name__, fake)


@pytest.mark.parametrize("api_type", list(RerankingType), ids=lambda t: t.value)
def test_every_declared_type_builds_a_backend(api_type, stand_in_backends):
    backend = create_reranking(RerankingConfig(api_type=api_type))
    assert type(backend).__name__ in _BACKENDS.values()


def test_an_undeclared_type_is_refused_when_the_config_is_read():
    """Not later, at first use, as ``Unsupported Model``."""
    with pytest.raises(ValidationError, match="cohere"):
        RerankingConfig.model_validate({"api_type": "cohere"})
