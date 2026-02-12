# Cài đặt và Thiết lập Hush

Hướng dẫn này sẽ đưa bạn từ zero đến chạy được workflow Hush đầu tiên.

---

## 1. Yêu cầu hệ thống

| Yêu cầu | Phiên bản |
|----------|-----------|
| Python   | >= 3.10   |
| pip hoặc uv | bất kỳ |
| git      | bất kỳ    |

**Kiểm tra:**

```bash
python3 --version
# Kết quả mong đợi: Python 3.10.x trở lên

git --version
# Kết quả mong đợi: git version 2.x.x
```

> **Khuyến nghị:** Dùng [uv](https://docs.astral.sh/uv/) thay pip — nhanh hơn rất nhiều.
> Cài uv: `curl -LsSf https://astral.sh/uv/install.sh | sh`

---

## 2. Cài đặt

Hush là **monorepo** với 3 packages riêng biệt:

| Package | Mô tả | Khi nào cần |
|---------|--------|-------------|
| `hush-core` | Workflow engine (GraphOp, FuncOp, BranchOp) | **Luôn cần** — đây là nền tảng |
| `hush-providers` | LLM, embedding, reranking providers | Khi dùng LLM, embedding, hoặc reranking |
| `hush-ops` | Langfuse, OpenTelemetry tracing | Khi cần tracing với backend bên ngoài |

> **Quan trọng:** `hush-providers` và `hush-ops` phụ thuộc vào `hush-core`, nên luôn cài `hush-core` trước.

#### Extras — kết hợp tuỳ ý

**hush-providers:**

| Extra | Provider |
|-------|----------|
| `openai` | OpenAI + Azure OpenAI (GPT-4o, GPT-4o-mini...) + OpenRouter + vLLM |
| `gemini` | Google Gemini |
| `bedrock` | AWS Bedrock |
| `embeddings` | ONNX embedding local |
| `rerankers` | ONNX reranking local |
| `onnx` | ONNX Runtime (embedding + reranking) |
| `huggingface` | Transformers + PyTorch (nặng ~2GB+) |
| `all-light` | Tất cả providers nhẹ (không có PyTorch) |
| `all` | Tất cả bao gồm PyTorch |

> OpenAI (bao gồm Azure OpenAI) đã có sẵn trong base dependencies của `hush-providers`, không cần thêm extra.

**hush-ops:**

| Extra | Backend |
|-------|---------|
| `langfuse` | Langfuse tracing |
| `otel` | OpenTelemetry |
| `all` | Langfuse + OpenTelemetry |

---

Chọn **một trong hai** cách cài đặt bên dưới.

### Cách A: Với pip

#### Bước 1 — Tạo project và virtual environment

```bash
mkdir my-hush-project && cd my-hush-project
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
```

#### Bước 2 — Cài đặt trực tiếp

```bash
# Tối thiểu — chỉ workflow engine
pip install "hush-core @ git+https://github.com/batman1m2001-cyber/Hush-ai.git#subdirectory=hush-core"

# Core + LLM providers + Langfuse tracing
pip install "hush-core @ git+https://github.com/batman1m2001-cyber/Hush-ai.git#subdirectory=hush-core"
pip install "hush-providers @ git+https://github.com/batman1m2001-cyber/Hush-ai.git#subdirectory=hush-providers"
pip install "hush-ops[langfuse] @ git+https://github.com/batman1m2001-cyber/Hush-ai.git#subdirectory=hush-ops"
```

#### Hoặc: Dùng requirements.txt

Tạo file `requirements.txt` trong thư mục project:

```txt
# requirements.txt
hush-core @ git+https://github.com/batman1m2001-cyber/Hush-ai.git#subdirectory=hush-core
hush-providers @ git+https://github.com/batman1m2001-cyber/Hush-ai.git#subdirectory=hush-providers
hush-ops[langfuse] @ git+https://github.com/batman1m2001-cyber/Hush-ai.git#subdirectory=hush-ops
```

Sau đó cài đặt:

```bash
pip install -r requirements.txt
```

> Bỏ dòng nào không cần. Chỉ `hush-core` là bắt buộc.

---

### Cách B: Với uv (khuyến nghị)

> [uv](https://docs.astral.sh/uv/) nhanh hơn pip rất nhiều. Cài uv: `curl -LsSf https://astral.sh/uv/install.sh | sh`

#### Bước 1 — Tạo project

```bash
uv init my-hush-project && cd my-hush-project
```

#### Bước 2a — Cài đặt trực tiếp

```bash
# Tối thiểu — chỉ workflow engine
uv pip install "hush-core @ git+https://github.com/batman1m2001-cyber/Hush-ai.git#subdirectory=hush-core"

# Core + LLM providers + Langfuse tracing
uv pip install "hush-core @ git+https://github.com/batman1m2001-cyber/Hush-ai.git#subdirectory=hush-core"
uv pip install "hush-providers @ git+https://github.com/batman1m2001-cyber/Hush-ai.git#subdirectory=hush-providers"
uv pip install "hush-ops[langfuse] @ git+https://github.com/batman1m2001-cyber/Hush-ai.git#subdirectory=hush-ops"
```

#### Hoặc: Dùng pyproject.toml (khuyến nghị cho dự án thực tế)

Thêm vào `pyproject.toml` (hoặc tạo mới):

```toml
[project]
name = "my-hush-project"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = [
    "hush-core",
    "hush-providers",
    "hush-ops[langfuse]",
]

[tool.uv.sources]
hush-core = { git = "https://github.com/batman1m2001-cyber/Hush-ai.git", subdirectory = "hush-core" }
hush-providers = { git = "https://github.com/batman1m2001-cyber/Hush-ai.git", subdirectory = "hush-providers" }
hush-ops = { git = "https://github.com/batman1m2001-cyber/Hush-ai.git", subdirectory = "hush-ops" }
```

Sau đó cài đặt:

```bash
uv sync
```

> Bỏ dòng nào không cần trong `dependencies` và `[tool.uv.sources]`. Chỉ `hush-core` là bắt buộc.

---

Kết quả mong đợi:

```
Successfully installed hush-core-0.1.0 ...
Successfully installed hush-providers-0.1.0 ...
Successfully installed hush-ops-0.1.0 ...
```

### 2.3 Kiểm tra cài đặt cơ bản

```bash
python3 -c "from hush.core import Hush, GraphOp; print('hush-core OK')"
python3 -c "from hush.providers import LLMOp; print('hush-providers OK')"
python3 -c "from hush.ops import LangfuseTracer; print('hush-ops OK')"
```

Kết quả mong đợi:

```
hush-core OK
hush-providers OK
hush-ops OK
```

Nếu thấy 3 dòng "OK" → cài đặt packages thành công. Tiếp tục thiết lập API keys.

---

## 3. Hiểu ResourceHub — trung tâm cấu hình của Hush

Trước khi thiết lập files, cần hiểu cách Hush kết nối đến LLM, embedding, tracing.

### Luồng khởi tạo

Khi bạn tạo một op dùng provider (`LLMOp.of()`, `EmbeddingOp.of()`, `RerankOp.of()`...), Hush tự động gọi `get_hub()` để tìm cấu hình:

```
Code: LLMOp.of(resource_key="gpt-4o", messages=...)
  ↓
get_hub()  →  singleton ResourceHub
  ↓
Tìm resources.yaml theo thứ tự:
  1. HUSH_CONFIG env var  (nếu có)
  2. ./resources.yaml     (thư mục hiện tại)
  3. ~/.hush/resources.yaml
  ↓
Đọc YAML → thay ${OPENAI_API_KEY} bằng os.environ
  ↓
hub.llm("gpt-4o")  →  tìm key "llm:gpt-4o" → tạo LLM client
```

### Điều này có nghĩa là gì?

**3 thứ phải có trước khi chạy bất kỳ workflow nào dùng LLM/embedding/tracing:**

| # | Cần gì | File | Chi tiết |
|---|--------|------|----------|
| 1 | **API keys + Hush config** | `.env` | API keys cho providers, đường dẫn `HUSH_CONFIG`, `HUSH_TRACES_DB`... |
| 2 | **Provider config** | `resources.yaml` | Định nghĩa từng provider: model nào, endpoint nào, dùng key nào |
| 3 | **Load env trước khi tạo op** | Trong code | Gọi `load_dotenv()` **trước** khi import/tạo op dùng provider |

> **Nếu thiếu bất kỳ thứ nào:** bạn sẽ gặp `RuntimeError: Cannot initialize global ResourceHub` hoặc `WARNING: Environment variable ... not found`.

---

## 4. Thiết lập file .env

### .env là gì?

File `.env` lưu **tất cả biến môi trường** mà Hush cần: API keys, đường dẫn config, đường dẫn traces DB, logging... Hush dùng `python-dotenv` để đọc file này.

### Tạo file .env

Repo đã có template sẵn. Copy và điền giá trị:

```bash
cp env.example .env
# Mở .env và điền API keys + cấu hình
```

### Danh sách biến môi trường

#### Hush system (cấu hình engine)

| Biến | Bắt buộc? | Mặc định | Mô tả |
|------|-----------|----------|-------|
| `HUSH_CONFIG` | Tuỳ chọn | `./resources.yaml` | Đường dẫn đến file `resources.yaml`. Set nếu chạy code từ thư mục khác nơi đặt file |
| `HUSH_TRACES_DB` | Tuỳ chọn | `~/.hush/traces.db` | Đường dẫn SQLite lưu traces (LocalTracer, background flush) |
| `LOG_LEVEL` | Tuỳ chọn | `WARNING` | Mức log: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `LOG_USE_RICH` | Tuỳ chọn | `auto` | Bật rich formatting cho logs |

#### LLM providers (điền key của provider bạn dùng)

| Biến | Bắt buộc? | Mô tả |
|------|-----------|-------|
| `OPENAI_API_KEY` | **Cần nếu dùng OpenAI** | Dùng cho `llm:gpt-4o`, `llm:gpt-4o-mini`, `embedding:openai` |
| `OPENROUTER_API_KEY` | **Cần nếu dùng OpenRouter** | Dùng cho `llm:or-claude-4-sonnet` — truy cập Claude, Llama, Mistral qua 1 API |
| `ANTHROPIC_API_KEY` | Tuỳ chọn | Anthropic API trực tiếp |
| `GOOGLE_API_KEY` | Tuỳ chọn | Google Gemini |
| `AZURE_OPENAI_API_KEY` | Tuỳ chọn | Azure OpenAI |
| `AZURE_OPENAI_ENDPOINT` | Tuỳ chọn | Azure endpoint URL |
| `AWS_ACCESS_KEY_ID` | Tuỳ chọn | AWS Bedrock |
| `AWS_SECRET_ACCESS_KEY` | Tuỳ chọn | AWS Bedrock |
| `DEEPINFRA_API_KEY` | Tuỳ chọn | DeepInfra (embedding BGE-M3) |
| `PINECONE_API_KEY` | Tuỳ chọn | Pinecone reranker |

#### Observability (tracing & monitoring)

| Biến | Bắt buộc? | Mô tả |
|------|-----------|-------|
| `LANGFUSE_PUBLIC_KEY` | **Cần nếu dùng Langfuse** | Langfuse public key |
| `LANGFUSE_SECRET_KEY` | **Cần nếu dùng Langfuse** | Langfuse secret key |
| `LANGFUSE_HOST` | **Cần nếu dùng Langfuse** | Langfuse host (mặc định `https://cloud.langfuse.com`) |

#### Enterprise / Internal (Keycloak auth)

| Biến | Bắt buộc? | Mô tả |
|------|-----------|-------|
| `KEYCLOAK_URL` | Tuỳ chọn | Keycloak token endpoint |
| `KEYCLOAK_CLIENT_NAME` | Tuỳ chọn | Keycloak client name |
| `KEYCLOAK_CLIENT_SECRET` | Tuỳ chọn | Keycloak client secret |
| `KEYCLOAK_REFRESH_INTERVAL` | Tuỳ chọn | Token refresh interval (giây) |
| `LLM_BASE_URL` | Tuỳ chọn | Base URL cho LLM qua Keycloak |
| `LLM_MODEL` | Tuỳ chọn | Model name qua Keycloak |

#### Local models (ONNX)

| Biến | Bắt buộc? | Mô tả |
|------|-----------|-------|
| `BGE_M3_EMBEDDING_PATH` | Tuỳ chọn | Đường dẫn ONNX embedding model |
| `BGE_M3_RERANKER_PATH` | Tuỳ chọn | Đường dẫn ONNX reranker model |

> **Tối thiểu để bắt đầu:** Chỉ cần `OPENAI_API_KEY` hoặc `OPENROUTER_API_KEY` là đủ chạy các ví dụ LLM.
> Các biến khác thêm khi cần dùng provider tương ứng.

### Lấy API keys ở đâu?

| Provider | Đăng ký | Trang lấy key |
|----------|---------|----------------|
| **OpenAI** | [platform.openai.com](https://platform.openai.com) | Settings → API Keys |
| **OpenRouter** | [openrouter.ai](https://openrouter.ai) | Keys (menu trái) |
| **Langfuse** | [cloud.langfuse.com](https://cloud.langfuse.com) | Settings → API Keys |

> **Lưu ý:** OpenRouter cho credit miễn phí khi đăng ký, phù hợp để test. OpenAI cần thêm phương thức thanh toán.

### Quan trọng

- **KHÔNG commit file `.env` lên git.** File `.gitignore` của Hush đã bao gồm `.env`.
- Mỗi người dùng tự tạo file `.env` riêng với keys của mình.
- Phải gọi `load_dotenv()` ở đầu code **trước** khi tạo op dùng provider.

Xem đầy đủ template: [`env.example`](../../env.example)

---

## 5. Thiết lập file resources.yaml

### resources.yaml là gì?

File `resources.yaml` là **cấu hình trung tâm** được đọc bởi **ResourceHub** (`get_hub()`). Mọi op dùng provider (LLM, embedding, reranking, tracing) đều tra cứu cấu hình từ file này.

Khi bạn viết `LLMOp.of(resource_key="gpt-4o", ...)`, ResourceHub tìm key `llm:gpt-4o` trong file này.

### Copy từ template có sẵn

Repo đã có file [`resources.yaml`](../../resources.yaml) ở thư mục gốc — đây là template đầy đủ với tất cả providers (LLM, embedding, reranking, Langfuse, OTEL, Keycloak).

```bash
# Nếu làm việc trong monorepo
# resources.yaml đã có sẵn ở thư mục gốc — không cần làm gì thêm

# Nếu tạo project riêng — copy template
cp resources.yaml /path/to/my-project/resources.yaml
```

Hoặc dùng phiên bản nhẹ hơn (chỉ OpenAI + OpenRouter + Langfuse):

```bash
cp hush-tutorial/docs/resources.starter.yaml /path/to/my-project/resources.yaml
```

### Cấu trúc file

Mỗi resource có dạng `category:tên_resource`:

```yaml
llm:gpt-4o:
  api_type: openai
  api_key: ${OPENAI_API_KEY}     # ← thay bằng giá trị từ .env lúc runtime
  base_url: https://api.openai.com/v1
  model: gpt-4o
```

| Category | Ý nghĩa | Ví dụ |
|----------|----------|-------|
| `keycloak` | Token provider (enterprise) | `keycloak:myapp` |
| `llm` | Model ngôn ngữ | `llm:gpt-4o-mini`, `llm:or-claude-4-sonnet` |
| `embedding` | Chuyển text → vector | `embedding:openai`, `embedding:bge-m3` |
| `reranking` | Xếp hạng lại kết quả | `reranking:bge-m3` |
| `langfuse` | Tracing với Langfuse | `langfuse:default` |
| `otel` | Tracing với OpenTelemetry | `otel:default` |

### Cú pháp biến môi trường

```yaml
api_key: ${OPENAI_API_KEY}          # Bắt buộc — warning nếu chưa set
base_url: ${MY_URL:http://default}  # Tuỳ chọn — dùng default nếu chưa set
```

### ResourceHub tìm resources.yaml ở đâu?

Theo thứ tự ưu tiên (code: `_get_global_hub()` trong `hush.core.registry`):

1. Biến môi trường `HUSH_CONFIG` (nếu có)
2. `./resources.yaml` (thư mục hiện tại — **phổ biến nhất**)
3. `~/.hush/resources.yaml` (thư mục home)

> **Tip:** Nếu chạy code từ thư mục khác với nơi đặt `resources.yaml`, set `HUSH_CONFIG` trong `.env`:
> ```dotenv
> HUSH_CONFIG=/path/to/resources.yaml
> ```

---

## 6. Traces database

Hush tự động lưu traces (lịch sử chạy workflow) vào SQLite database.

| Config | Mặc định | Cách thay đổi |
|--------|----------|---------------|
| Đường dẫn DB | `~/.hush/traces.db` | Set `HUSH_TRACES_DB` trong `.env` |

Traces được dùng bởi:
- **LocalTracer** — lưu traces cho debug local
- **Background process** — buffer traces trước khi flush đến Langfuse/OTEL
- **VS Code Trace Viewer** — đọc DB để hiển thị traces

Không cần tạo DB thủ công — Hush tự tạo khi chạy workflow đầu tiên.

---

## 7. Kiểm tra toàn bộ

### Test 1 — Workflow cơ bản (không cần API key, không cần resources.yaml)

```bash
python3 -c "
import asyncio
from hush.core import Hush, GraphOp, op, START, END, PARENT

@op
def hello():
    return {'message': 'Hello from Hush!'}

async def main():
    with GraphOp(name='hello-hush') as graph:
        step = hello()
        START >> step >> END

    engine = Hush(graph)
    result = await engine.run(inputs={})
    print(f'Result: {result[\"message\"]}')

asyncio.run(main())
"
```

Kết quả mong đợi:

```
Result: Hello from Hush!
```

### Test 2 — Kết nối LLM (cần .env + resources.yaml)

```bash
python3 -c "
import asyncio
from dotenv import load_dotenv
load_dotenv()  # BẮT BUỘC: load env vars trước khi tạo op

from hush.core import Hush, GraphOp, START, END, PARENT
from hush.providers import ChainOp

async def main():
    with GraphOp(name='test-llm') as graph:
        chat = ChainOp.of(
            resource_key='gpt-4o-mini',  # ← tra cứu trong resources.yaml
            template='Say hello in exactly 3 words.',
        )
        START >> chat >> END

    engine = Hush(graph)
    result = await engine.run(inputs={})
    print(f'LLM response: {result[\"content\"]}')

asyncio.run(main())
"
```

Kết quả mong đợi (nội dung sẽ khác mỗi lần):

```
LLM response: Hello, dear friend!
```

### Test 3 — Kiểm tra Langfuse tracing

```bash
python3 -c "
import asyncio
from dotenv import load_dotenv
load_dotenv()

from hush.core import Hush, GraphOp, op, START, END, PARENT
from hush.ops import LangfuseTracer

@op
def hello():
    return {'message': 'Tracing works!'}

async def main():
    with GraphOp(name='test-tracing') as graph:
        step = hello()
        START >> step >> END

    tracer = LangfuseTracer(resource_key='langfuse:default')
    engine = Hush(graph)
    result = await engine.run(inputs={}, tracer=tracer)
    print(f'Result: {result[\"message\"]}')
    print('Check Langfuse dashboard for the trace.')

asyncio.run(main())
"
```

Mở [cloud.langfuse.com](https://cloud.langfuse.com) → Traces → bạn sẽ thấy trace `test-tracing`.

---

## 8. Troubleshooting

### `RuntimeError: Cannot initialize global ResourceHub`

ResourceHub không tìm được `resources.yaml`. Kiểm tra:

1. File `resources.yaml` có ở thư mục hiện tại không? (`ls resources.yaml`)
2. Hoặc set `HUSH_CONFIG` trong `.env` trỏ đến đúng đường dẫn
3. Đã gọi `load_dotenv()` trước khi import op chưa?

### `ModuleNotFoundError: No module named 'hush'`

Bạn chưa cài packages hoặc chưa activate virtual environment:

```bash
source .venv/bin/activate
uv pip install "hush-core @ git+https://github.com/batman1m2001-cyber/Hush-ai.git#subdirectory=hush-core"
```

### `WARNING: Environment variable OPENAI_API_KEY not found`

File `.env` chưa được tạo hoặc chưa load. Kiểm tra:

1. File `.env` có tồn tại ở thư mục gốc project không?
2. Code có gọi `load_dotenv()` **trước** khi tạo op dùng provider không?

### `openai.AuthenticationError: Incorrect API key`

API key sai hoặc hết hạn. Kiểm tra lại key trong file `.env`.

### `Connection error` hoặc `timeout`

- Kiểm tra kết nối internet
- Nếu dùng proxy/VPN, đảm bảo nó cho phép kết nối đến API providers

### Python version < 3.10

```
SyntaxError: ... match/case ... (hoặc lỗi type hint)
```

Cài Python 3.10+ và tạo lại virtual environment.

---

## Tiếp theo

Sau khi hoàn thành thiết lập, bạn đã sẵn sàng bắt đầu:

- [Quickstart](02-quickstart.md) — Chạy workflow đầu tiên
- [Core Concepts](03-core-concepts.md) — Hiểu các khái niệm cốt lõi
- [Tổng quan](00-tong-quan.md) — Xem toàn bộ danh sách tutorials
