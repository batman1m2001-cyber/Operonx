# Config Loading - YAML Parsing & Env Interpolation

## Overview

ResourceHub hỗ trợ nhiều storage backends và env interpolation trong configs.

## Storage Backends

### YamlConfigStorage

```python
class YamlConfigStorage(ConfigStorage):
    def __init__(self, path: Path):
        self._path = path

    def load_one(self, key: str) -> Optional[Dict]:
        data = yaml.safe_load(self._path.read_text())
        return data.get(key)

    def load_all(self) -> Dict[str, Dict]:
        return yaml.safe_load(self._path.read_text())
```

### JsonConfigStorage

```python
class JsonConfigStorage(ConfigStorage):
    def __init__(self, path: Path):
        self._path = path

    def load_one(self, key: str) -> Optional[Dict]:
        data = json.loads(self._path.read_text())
        return data.get(key)
```

## YAML Format

```yaml
# resources.yaml

# LLM configs
llm:gpt-4:
  type: openai
  model: gpt-4
  temperature: 0.7
  api_key: ${OPENAI_API_KEY}  # Env interpolation

llm:azure-gpt-4:
  type: azure
  model: gpt-4
  deployment: my-deployment
  api_key: ${AZURE_OPENAI_KEY}
  endpoint: ${AZURE_OPENAI_ENDPOINT}

# Embedding configs
embedding:text-3-small:
  type: openai-embedding
  model: text-embedding-3-small

# Database configs
redis:default:
  type: redis
  host: ${REDIS_HOST:localhost}  # Default value
  port: ${REDIS_PORT:6379}
  password: ${REDIS_PASSWORD}
```

## Environment Interpolation

YamlModel xử lý env vars:

```python
class YamlModel(BaseModel):
    @model_validator(mode='before')
    def interpolate_env(cls, values):
        return _interpolate_dict(values)

def _interpolate_dict(d: Dict) -> Dict:
    result = {}
    for k, v in d.items():
        if isinstance(v, str) and v.startswith("${"):
            result[k] = _resolve_env(v)
        elif isinstance(v, dict):
            result[k] = _interpolate_dict(v)
        else:
            result[k] = v
    return result

def _resolve_env(expr: str) -> str:
    # ${VAR} or ${VAR:default}
    match = re.match(r'\$\{(\w+)(?::(.+))?\}', expr)
    if match:
        var_name = match.group(1)
        default = match.group(2)
        return os.getenv(var_name, default)
    return expr
```

## Config Validation

Sử dụng Pydantic validation:

```python
@register_config("openai", category="llm")
class OpenAIConfig(YamlModel):
    model: str
    temperature: float = Field(ge=0, le=2, default=0.7)
    max_tokens: Optional[int] = Field(gt=0, default=None)
    api_key: Optional[str] = None

    @field_validator('model')
    def validate_model(cls, v):
        valid_models = ['gpt-4', 'gpt-4-turbo', 'gpt-3.5-turbo']
        if v not in valid_models:
            raise ValueError(f"Invalid model: {v}")
        return v
```

## Hot Reload

Storage có thể watch file changes:

```python
class YamlConfigStorage(ConfigStorage):
    def __init__(self, path: Path, watch: bool = False):
        self._path = path
        self._last_modified = None

        if watch:
            self._start_watcher()

    def _check_reload(self):
        mtime = self._path.stat().st_mtime
        if mtime != self._last_modified:
            self._last_modified = mtime
            self._reload()
```
