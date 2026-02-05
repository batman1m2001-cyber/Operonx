# ResourceHub Design & Singleton

## Overview

`ResourceHub` là centralized registry quản lý tất cả resources (LLM, embedding, database connections, etc.) với lazy loading và pluggable storage.

Location: `hush-core/hush/core/registry/resource_hub.py`

## Class Definition

```python
class ResourceHub:
    _instance: ClassVar[Optional['ResourceHub']] = None

    def __init__(self, storage: ConfigStorage):
        self._storage = storage
        self._cache: Dict[str, CacheEntry] = {}
```

## Factory Methods

```python
# From YAML file
hub = ResourceHub.from_yaml("configs/resources.yaml")

# From JSON file
hub = ResourceHub.from_json("configs/resources.json")

# Singleton pattern
ResourceHub.set_instance(hub)
hub = ResourceHub.instance()
```

## Lazy Loading

Resources chỉ được khởi tạo khi truy cập lần đầu:

```python
def get(self, key: str) -> Any:
    # Return cached instance if available
    if key in self._cache and self._cache[key].instance is not None:
        return self._cache[key].instance

    # Load config from storage
    config = self._load_config(key)
    if not config:
        raise KeyError(f"Resource '{key}' not found")

    # Lazy initialize resource
    instance = REGISTRY.create(config)
    self._cache[key].instance = instance

    return instance
```

## Type-specific Accessors

```python
# LLM
llm = hub.llm("gpt-4")           # llm:gpt-4
llm = hub.llm("azure:gpt-4")     # llm:azure:gpt-4

# Embedding
embedding = hub.embedding("text-embedding-3-small")

# Reranker
reranker = hub.reranker("bge-reranker")

# Database clients
redis = hub.redis("default")
mongo = hub.mongo("main")
milvus = hub.milvus("vectors")
```

## Config Format

```yaml
# resources.yaml
llm:gpt-4:
  type: openai
  model: gpt-4
  temperature: 0.7

embedding:text-embedding-3-small:
  type: openai-embedding
  model: text-embedding-3-small

redis:default:
  type: redis
  host: localhost
  port: 6379
```

## CacheEntry

```python
@dataclass
class CacheEntry:
    config: YamlModel    # Parsed config
    instance: Any = None # Lazy loaded instance
```

## Health Check

```python
result = hub.health_check()

if not result.healthy:
    print(f"Unhealthy resources: {result.failed}")
    print(f"Errors: {result.errors}")
```

## Global Hub Shortcut

```python
from hush.core.registry import get_hub

llm = get_hub().llm("gpt-4")
```
