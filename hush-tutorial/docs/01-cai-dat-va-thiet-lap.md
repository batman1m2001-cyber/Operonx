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

### 2.1 Tạo thư mục project và virtual environment

```bash
mkdir my-hush-project && cd my-hush-project
```

**Với uv (khuyến nghị):**

```bash
uv venv
source .venv/bin/activate
```

**Với pip:**

```bash
python3 -m venv .venv
source .venv/bin/activate
```

> Trên Windows, dùng `.venv\Scripts\activate` thay cho `source .venv/bin/activate`.

Sau khi activate, bạn sẽ thấy `(.venv)` ở đầu dòng terminal.

### 2.2 Cài đặt Hush từ GitHub

Hush dùng meta-package `hush-ai` — bạn chọn **extras** phù hợp với nhu cầu.

Cú pháp chung:

```bash
uv pip install "hush-ai[EXTRAS] @ git+https://github.com/batman1m2001-cyber/Hush-ai.git#subdirectory=hush-ai"
```

> **Dùng pip thay uv?** Thay `uv pip install` bằng `pip install`.

#### Tiers — chọn 1 làm base

| Extra | Bao gồm | Khi nào dùng |
|-------|----------|--------------|
| `core` | hush-core | Chỉ cần workflow engine, không cần LLM |
| `standard` | core + hush-providers | Dùng LLM qua API (OpenAI, OpenRouter...) |
| `all` | standard + tất cả providers nhẹ + observability | **Khuyến nghị** — đầy đủ cho phát triển |
| `full` | all + PyTorch + Transformers | Chạy model local (nặng ~2GB+) |

#### LLM Providers — kết hợp tuỳ ý

| Extra | Provider |
|-------|----------|
| `openai` | OpenAI (GPT-4o, GPT-4o-mini...) + OpenRouter + vLLM |
| `azure` | Azure OpenAI |
| `gemini` | Google Gemini |

#### Local Inference

| Extra | Mô tả |
|-------|-------|
| `onnx` | ONNX Runtime (embedding, reranking local) |
| `huggingface` | Transformers + PyTorch (nặng) |

#### Observability — kết hợp tuỳ ý

| Extra | Backend |
|-------|---------|
| `langfuse` | Langfuse tracing |
| `otel` | OpenTelemetry |

#### Development

| Extra | Mô tả |
|-------|-------|
| `dev` | Tất cả providers nhẹ + observability + pytest, black, ruff |

---

**Ví dụ cài đặt:**

```bash
# Khuyến nghị — đầy đủ cho phát triển
uv pip install "hush-ai[all] @ git+https://github.com/batman1m2001-cyber/Hush-ai.git#subdirectory=hush-ai"

# Nhẹ hơn — chỉ OpenAI + Langfuse
uv pip install "hush-ai[openai,langfuse] @ git+https://github.com/batman1m2001-cyber/Hush-ai.git#subdirectory=hush-ai"

# Tối thiểu — chỉ workflow engine
uv pip install "hush-ai[core] @ git+https://github.com/batman1m2001-cyber/Hush-ai.git#subdirectory=hush-ai"

# Kết hợp tự do — OpenAI + Gemini + Langfuse
uv pip install "hush-ai[openai,gemini,langfuse] @ git+https://github.com/batman1m2001-cyber/Hush-ai.git#subdirectory=hush-ai"
```

Kết quả mong đợi (ví dụ với tier `all`):

```
Successfully installed hush-core-0.1.0 hush-providers-0.1.0 hush-observability-0.1.0 ...
```

### 2.3 Kiểm tra cài đặt cơ bản

```bash
python3 -c "from hush.core import Hush, GraphNode; print('hush-core OK')"
python3 -c "from hush.providers import LLMNode; print('hush-providers OK')"
python3 -c "from hush.observability import LangfuseTracer; print('hush-observability OK')"
```

Kết quả mong đợi:

```
hush-core OK
hush-providers OK
hush-observability OK
```

Nếu thấy 3 dòng "OK" → cài đặt packages thành công. Tiếp tục thiết lập API keys.

---

## 3. Thiết lập file .env

### .env là gì?

File `.env` lưu các **API keys** và **cấu hình bí mật** — những thứ không được commit lên git. Hush dùng thư viện `python-dotenv` để đọc file này.

### Tạo file .env

Tạo file `.env` ở thư mục gốc project và điền API keys của bạn:

```dotenv
# OpenAI
OPENAI_API_KEY=sk-proj-your-actual-key

# OpenRouter
OPENROUTER_API_KEY=sk-or-v1-your-actual-key

# Langfuse
LANGFUSE_PUBLIC_KEY=pk-lf-your-actual-key
LANGFUSE_SECRET_KEY=sk-lf-your-actual-key
LANGFUSE_HOST=https://cloud.langfuse.com
```

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

---

## 4. Thiết lập file resources.yaml

### resources.yaml là gì?

File `resources.yaml` là **cấu hình trung tâm** của Hush. Nó định nghĩa tất cả providers mà workflow của bạn sử dụng: LLM nào, embedding nào, tracing ở đâu.

Khi bạn viết `hub.llm("gpt-4o-mini")` trong code, Hush sẽ tìm config `llm:gpt-4o-mini` trong file này.

### Tạo file resources.yaml

Tạo file `resources.yaml` ở thư mục gốc project với nội dung sau:

```yaml
# LLM — OpenRouter (Claude Sonnet, chi phí thấp)
llm:or-claude-4-sonnet:
  api_type: openai
  api_key: ${OPENROUTER_API_KEY}
  base_url: https://openrouter.ai/api/v1
  model: anthropic/claude-sonnet-4

# LLM — OpenAI GPT-4o-mini (nhanh, rẻ, phù hợp test)
llm:gpt-4o-mini:
  api_type: openai
  api_key: ${OPENAI_API_KEY}
  base_url: https://api.openai.com/v1
  model: gpt-4o-mini

# LLM — OpenAI GPT-4o
llm:gpt-4o:
  api_type: openai
  api_key: ${OPENAI_API_KEY}
  base_url: https://api.openai.com/v1
  model: gpt-4o

# Embedding — OpenAI
embedding:openai:
  api_type: openai
  api_key: ${OPENAI_API_KEY}
  base_url: https://api.openai.com/v1/embeddings
  model: text-embedding-3-small
  dimensions: 1536

# Langfuse — tracing & monitoring
langfuse:default:
  public_key: ${LANGFUSE_PUBLIC_KEY}
  secret_key: ${LANGFUSE_SECRET_KEY}
  host: ${LANGFUSE_HOST}
  enabled: true
  sample_rate: 1.0
```

Cú pháp `${TÊN_BIẾN}` sẽ được Hush tự động thay bằng giá trị từ file `.env`.

### Cấu trúc file

Mỗi resource có dạng:

```yaml
category:tên_resource:
  property1: value
  property2: ${BIẾN_MÔI_TRƯỜNG}
```

| Category | Ý nghĩa | Ví dụ |
|----------|----------|-------|
| `llm` | Model ngôn ngữ | `llm:gpt-4o-mini` |
| `embedding` | Chuyển text → vector | `embedding:openai` |
| `reranking` | Xếp hạng lại kết quả tìm kiếm | `reranking:bge-m3` |
| `langfuse` | Tracing với Langfuse | `langfuse:default` |
| `otel` | Tracing với OpenTelemetry | `otel:default` |

### Cú pháp biến môi trường

```yaml
api_key: ${OPENAI_API_KEY}          # Bắt buộc — warning nếu chưa set
base_url: ${MY_URL:http://default}  # Tuỳ chọn — dùng default nếu chưa set
```

### Hush tìm resources.yaml ở đâu?

Theo thứ tự ưu tiên:

1. Biến môi trường `HUSH_CONFIG` (nếu có)
2. `./resources.yaml` (thư mục hiện tại)
3. `~/.hush/resources.yaml` (thư mục home)

Vì chúng ta đã copy file vào thư mục gốc project → Hush sẽ tự tìm thấy (rule 2).

---

## 5. Kiểm tra toàn bộ

### Test 1 — Workflow cơ bản (không cần API key)

Chạy lệnh sau để kiểm tra hush-core hoạt động:

```bash
python3 -c "
import asyncio
from hush.core import Hush, GraphNode, CodeNode, START, END, PARENT

async def main():
    with GraphNode(name='hello-hush') as graph:
        hello = CodeNode(
            name='hello',
            code_fn=lambda: {'message': 'Hello from Hush!'},
            outputs={'message': PARENT}
        )
        START >> hello >> END

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

✓ Nếu thấy dòng trên → hush-core hoạt động.

### Test 2 — Kết nối LLM (cần API key)

```bash
python3 -c "
import asyncio
from dotenv import load_dotenv
load_dotenv()

from hush.core import Hush, GraphNode, START, END, PARENT
from hush.providers import PromptNode, LLMNode

async def main():
    with GraphNode(name='test-llm') as graph:
        prompt = PromptNode(
            name='prompt',
            messages=[{'role': 'user', 'content': 'Say hello in exactly 3 words.'}],
        )
        llm = LLMNode(
            name='llm',
            resource='llm:gpt-4o-mini',
            inputs={'messages': prompt['messages']},
            outputs={'content': PARENT['answer']},
        )
        START >> prompt >> llm >> END

    engine = Hush(graph)
    result = await engine.run(inputs={})
    print(f'LLM response: {result[\"answer\"]}')

asyncio.run(main())
"
```

Kết quả mong đợi (nội dung sẽ khác mỗi lần):

```
LLM response: Hello, dear friend!
```

✓ Nếu thấy response từ LLM → kết nối API thành công.

### Test 3 — Kiểm tra Langfuse tracing

```bash
python3 -c "
import asyncio
from dotenv import load_dotenv
load_dotenv()

from hush.core import Hush, GraphNode, CodeNode, START, END, PARENT
from hush.observability import LangfuseTracer

async def main():
    with GraphNode(name='test-tracing') as graph:
        hello = CodeNode(
            name='hello',
            code_fn=lambda: {'message': 'Tracing works!'},
            outputs={'message': PARENT}
        )
        START >> hello >> END

    tracer = LangfuseTracer(resource='langfuse:default')
    engine = Hush(graph)
    result = await engine.run(inputs={}, tracer=tracer)
    print(f'Result: {result[\"message\"]}')
    print('Check Langfuse dashboard for the trace.')

asyncio.run(main())
"
```

Kết quả mong đợi:

```
Result: Tracing works!
Check Langfuse dashboard for the trace.
```

✓ Mở [cloud.langfuse.com](https://cloud.langfuse.com) → Traces → bạn sẽ thấy trace `test-tracing`.

---

## 6. Troubleshooting

### `ModuleNotFoundError: No module named 'hush'`

Bạn chưa cài packages hoặc chưa activate virtual environment:

```bash
source .venv/bin/activate
uv pip install "hush-ai[all] @ git+https://github.com/batman1m2001-cyber/Hush-ai.git#subdirectory=hush-ai"
```

### `WARNING: Environment variable OPENAI_API_KEY not found`

File `.env` chưa được tạo hoặc chưa load. Kiểm tra:

1. File `.env` có tồn tại ở thư mục gốc project không?
2. Code có gọi `load_dotenv()` trước khi import hush không?

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
