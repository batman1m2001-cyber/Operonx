# Plugin Architecture

## Overview

Hush sử dụng plugin system để external packages có thể đăng ký config types và factories.

Location: `hush-core/hush/core/registry/config_registry.py`

## ConfigRegistry

```python
class ConfigRegistry:
    """Global registry for config classes and factories."""

    _configs: Dict[str, type] = {}           # type_name → config_class
    _factories: Dict[type, Callable] = {}    # config_class → factory_fn
    _category_map: Dict[str, str] = {}       # type_name → category
```

## Registering Configs

### Decorator

```python
from hush.core.registry import register_config

@register_config("openai", category="llm")
class OpenAIConfig(YamlModel):
    model: str
    temperature: float = 0.7
    api_key: str = None
```

### Manual

```python
from hush.core.registry import REGISTRY

REGISTRY.register("openai", OpenAIConfig, category="llm")
```

## Registering Factories

```python
from hush.core.registry import register_factory

@register_factory(OpenAIConfig)
def create_openai(config: OpenAIConfig):
    from openai import OpenAI
    return OpenAI(api_key=config.api_key or os.getenv("OPENAI_API_KEY"))
```

## Plugin Entry Point

External packages đăng ký qua setuptools entry points:

```toml
# pyproject.toml
[project.entry-points."hush.plugins"]
my_plugin = "my_package:register_plugin"
```

```python
# my_package/__init__.py
def register_plugin():
    from hush.core.registry import register_config, register_factory

    @register_config("my-llm", category="llm")
    class MyLLMConfig(YamlModel):
        ...

    @register_factory(MyLLMConfig)
    def create_my_llm(config):
        ...
```

## Category Namespace

Categories prevent name collisions:

```python
# Same type name, different categories
REGISTRY.register("default", RedisConfig, category="redis")
REGISTRY.register("default", MongoConfig, category="mongo")

# Lookup with category
config_class = REGISTRY.get_class("default", category="redis")  # RedisConfig
```

## Creating Instances

```python
# From config object
config = OpenAIConfig(model="gpt-4")
instance = REGISTRY.create(config)

# From ResourceHub
hub = ResourceHub.from_yaml("resources.yaml")
instance = hub.get("llm:gpt-4")  # Uses registered factory
```
