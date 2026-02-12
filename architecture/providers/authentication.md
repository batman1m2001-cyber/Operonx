# Authentication System

## Overview

Hush cung cấp hệ thống authentication pluggable để truy cập các API được bảo vệ. Hiện tại hỗ trợ Keycloak OAuth token provider với background refresh tự động.

Location: `hush-providers/hush/providers/auth/`

## Tại sao không phải Workflow Op?

Authentication là **infrastructure component**, không phải workflow op:

- Token có thời gian sống dài (hours), không theo request scope
- Background refresh chạy độc lập với workflow execution
- Được sử dụng tại provider level (khi tạo LLM/embedding client), không phải op level

## Kiến trúc

```
┌───────────────────────────────────────────┐
│              ResourceHub                   │
│  ┌─────────────────────────────────────┐  │
│  │  keycloak:myapp → KeycloakTokenConfig│  │
│  └────────────────────┬────────────────┘  │
└───────────────────────┼───────────────────┘
                        │
                        ▼
            ┌───────────────────────┐
            │ KeycloakTokenProvider │
            │                       │
            │ ┌─ get_token() ─────┐ │
            │ │ Lock              │ │
            │ │ Check cache       │ │
            │ │ Fetch if expired  │ │
            │ └───────────────────┘ │
            │                       │
            │ ┌─ Background ──────┐ │
            │ │ Daemon thread     │ │
            │ │ Proactive refresh │ │
            │ │ Error isolation   │ │
            │ └───────────────────┘ │
            └───────────────────────┘
```

## KeycloakTokenConfig

Pydantic config được load từ YAML:

```python
class KeycloakTokenConfig(YamlModel):
    _category: ClassVar[str] = "keycloak"

    url: str                              # Token endpoint
    name: str                             # Client name/app ID
    secret: str                           # Client secret
    token_path: str = "accessToken"       # JSON path cho token trong response
    expires_in_path: str = "expiresIn"    # JSON path cho TTL (optional)
    refresh_interval: float = 3600.0      # Background refresh interval (1h)
    refresh_buffer: float = 300.0         # Refresh trước khi hết hạn (5min)
```

YAML config:

```yaml
keycloak:myapp:
  url: https://identity.example.com/client/connect
  name: my_app
  secret: ${KC_SECRET}
  token_path: accessToken
  expires_in_path: expiresIn
  refresh_interval: 3600
```

## KeycloakTokenProvider

### Lifecycle

```
1. __init__()
   └── Lưu config, khởi tạo lock/event, register instance

2. get_token() [lần đầu]
   ├── _ensure_started()  → Khởi động background daemon thread
   ├── Lock acquire
   ├── _fetch_token()     → HTTP POST đến Keycloak endpoint
   │   ├── Extract token theo token_path
   │   └── Set _expires_at = now + expires_in - refresh_buffer
   └── Return token

3. get_token() [lần sau]
   ├── Lock acquire
   ├── Check: _token exists AND time.time() < _expires_at?
   │   ├── YES → Return cached token (O(1))
   │   └── NO  → _fetch_token() và return
   └── Lock release

4. Background refresh loop (daemon thread)
   ├── Wait refresh_interval (hoặc shutdown signal)
   ├── Check needs_refresh: no token OR near expiry
   │   ├── YES → _fetch_token() (trong lock)
   │   └── NO  → Skip
   └── Loop (cho đến shutdown)

5. Shutdown (atexit hoặc manual)
   ├── Set shutdown_event
   ├── Join thread (timeout 2s)
   └── Clear started flag
```

### Thread Safety

- **Lock** (`threading.Lock`): Bảo vệ `_token` và `_expires_at` khi read/write
- **Event** (`threading.Event`): Signal graceful shutdown cho background thread
- **Daemon thread**: Tự động stop khi main program exit

### Error Isolation

Background refresh errors được log, **không raise**:

```python
except Exception as e:
    LOGGER.warning("Background refresh failed: %s", e)
    # Không crash — on-demand fetch là fallback
```

Nếu background refresh thất bại, `get_token()` sẽ tự fetch khi token hết hạn.

### Instance Tracking

```python
class KeycloakTokenProvider:
    _instances: dict[str, "KeycloakTokenProvider"] = {}
    _instances_lock = threading.Lock()
```

- Tất cả instances được track trong class-level dict
- `shutdown_all()` class method dọn dẹp tất cả
- `atexit.register(shutdown_all)` đảm bảo cleanup khi program exit

### invalidate()

Force re-fetch token lần tiếp theo:

```python
provider.invalidate()  # Clear cache
token = provider.get_token()  # Sẽ fetch mới
```

## AuthFactory

Factory pattern để tạo auth provider từ config:

```python
from hush.providers.auth.factory import AuthFactory

provider = AuthFactory.create(config)  # KeycloakTokenConfig → KeycloakTokenProvider
```

## Thêm Auth Backend Mới

1. Tạo config trong `auth/config.py`:

```python
class MyAuthConfig(YamlModel):
    _category: ClassVar[str] = "my_auth"
    api_key: str
    endpoint: str
```

2. Tạo provider trong `auth/my_auth.py`:

```python
class MyAuthProvider:
    def __init__(self, config: MyAuthConfig):
        self.config = config

    def get_token(self) -> str:
        # Fetch token từ endpoint
        pass

    def shutdown(self):
        # Cleanup resources
        pass
```

3. Register trong `auth/factory.py`:

```python
AuthFactory.register("my_auth", MyAuthProvider)
```

4. Register plugin trong `registry/auth_plugin.py`

## Xem thêm

- [ResourceHub](../resources/resource-hub.md) - Config loading và management
- [Plugin System](../resources/plugin-system.md) - Plugin registration
- [Config Loading](../resources/config-loading.md) - YAML parsing và env interpolation
