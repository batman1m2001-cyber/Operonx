# Rust Crate Types: cdylib vs rlib — Giải thích CI linker issue

## Vấn đề

CI chạy `cargo test` trên rush-core → **lỗi linker** trên cả Windows và Linux:

```
error LNK1169: one or more multiply defined symbols found   (Windows)
error: linking with `cc` failed                              (Linux)
```

Nguyên nhân: rush-core chỉ build dạng `cdylib` (thư viện cho Python).

---

## Crate Type là gì?

Khi Rust build code, nó "đóng gói" output theo format khác nhau.
`crate-type` trong `Cargo.toml` quyết định format nào:

```
┌─────────────────────────────────────────────────────────────┐
│                    Rust Source Code                          │
│              (rush-core/src/*.rs)                            │
└──────────────────────┬──────────────────────────────────────┘
                       │
              cargo build / cargo test
                       │
          ┌────────────┴────────────┐
          ▼                         ▼
   ┌─────────────┐          ┌─────────────┐
   │   cdylib    │          │    rlib     │
   │             │          │             │
   │ .pyd / .so  │          │   .rlib     │
   │ .dll        │          │             │
   └──────┬──────┘          └──────┬──────┘
          │                        │
          ▼                        ▼
   ┌─────────────┐          ┌─────────────┐
   │   Python    │          │    Rust     │
   │             │          │             │
   │ import      │          │ cargo test  │
   │ rush_core   │          │ #[test] fn  │
   └─────────────┘          └─────────────┘
```

### cdylib — "C Dynamic Library"

- Output: `rush_core.pyd` (Win) / `rush_core.so` (Linux)
- Dùng bởi: **Python** (`import rush_core`)
- Đặc điểm: Phải link đầy đủ PyO3 + Python symbols
- Build bằng: `maturin develop --release`

### rlib — "Rust Library"

- Output: `librush_core.rlib`
- Dùng bởi: **Rust** (cargo test, các crate khác depend vào)
- Đặc điểm: Không cần link Python, chạy thuần Rust
- Build bằng: `cargo test`, `cargo build`

---

## Flow hiện tại (LỖI)

```toml
# rush-core/Cargo.toml
[lib]
crate-type = ["cdylib"]          # ← CHỈ có cdylib
```

```
┌─ cargo test ────────────────────────────────────────────────┐
│                                                             │
│  1. Compile rush-core source      ✅ OK                    │
│  2. Build test binary             ✅ OK                    │
│  3. Link test binary              ❌ LỖI!                  │
│     │                                                       │
│     └─→ crate-type chỉ có cdylib                           │
│         → Cargo link dạng cdylib (cần Python symbols)       │
│         → PyO3 + ONNX runtime → xung đột symbols           │
│         → LINKER ERROR                                      │
│                                                             │
│  Kết quả: 8 unit test KHÔNG CHẠY ĐƯỢC                      │
└─────────────────────────────────────────────────────────────┘

┌─ maturin develop --release ─────────────────────────────────┐
│                                                             │
│  1. Compile rush-core source      ✅ OK                    │
│  2. Build cdylib (.pyd/.so)       ✅ OK (release mode)     │
│  3. Install vào Python venv       ✅ OK                    │
│                                                             │
│  Kết quả: Python import rush_core OK                        │
│           136 pytest tests PASS                             │
└─────────────────────────────────────────────────────────────┘
```

**Tóm lại:** `maturin` build OK, nhưng `cargo test` lỗi vì thiếu format rlib.

---

## Flow sau khi fix

```toml
# rush-core/Cargo.toml
[lib]
crate-type = ["cdylib", "rlib"]   # ← Thêm rlib
```

```
┌─ cargo test ────────────────────────────────────────────────┐
│                                                             │
│  1. Compile rush-core source      ✅ OK                    │
│  2. Build test binary             ✅ OK                    │
│  3. Link test binary              ✅ OK!                   │
│     │                                                       │
│     └─→ crate-type có rlib                                  │
│         → Cargo link dạng rlib (thuần Rust)                 │
│         → KHÔNG cần Python symbols                          │
│         → Không xung đột                                    │
│                                                             │
│  Kết quả: 8 unit test CHẠY OK                              │
└─────────────────────────────────────────────────────────────┘

┌─ maturin develop --release ─────────────────────────────────┐
│                                                             │
│  (Không thay đổi — maturin luôn dùng cdylib)               │
│                                                             │
│  Kết quả: Python import rush_core OK                        │
│           136 pytest tests PASS                             │
└─────────────────────────────────────────────────────────────┘
```

---

## Thay đổi cần làm

### 1. rush-core/Cargo.toml (đổi 1 dòng)

```diff
 [lib]
 name = "rush_core"
-crate-type = ["cdylib"]
+crate-type = ["cdylib", "rlib"]
```

### 2. .github/workflows/rust-runtime.yaml (đổi cargo test command)

```diff
-     - name: Run Rust unit tests (rush-providers)
-       working-directory: rush-core
-       run: cargo test -p rush-providers
+     - name: Run Rust unit tests
+       working-directory: rush-core
+       run: cargo test --lib
```

`cargo test --lib` chỉ chạy `#[cfg(test)]` trong `src/` — không chạy integration test hay benchmark.

### 3. Không thay đổi gì khác

- Maturin build: không ảnh hưởng
- Python tests: không ảnh hưởng
- Các hush-* package: không ảnh hưởng
- Cấu trúc thư mục: không đổi

---

## 8 Unit Tests được giữ lại

| File | Test | Kiểm tra gì |
|------|------|-------------|
| plugins/mod.rs | `test_parse_plugin_spec` | Parse `"lib.so::func"` → `("lib.so", "func")` |
| plugins/mod.rs | `test_is_shared_lib` | `.so`/`.dylib`/`.dll` → true, `.rs` → false |
| plugins/mod.rs | `test_parse_crate_name` | Đọc `name = "my-crate"` từ Cargo.toml |
| plugins/mod.rs | `test_lib_output_path` | Tính đường dẫn output: `target/release/libmy_crate.so` |
| config.rs | `test_config_structs_exist` | Các struct config có thể khởi tạo được |
| config.rs | `test_parse_ref_arg_literal` | JSON literal → `RefArg::Literal` |
| config.rs | `test_parse_ref_arg_nested_ref` | `{"__ref__": {...}}` → `RefArg::NestedRef` |
| config.rs | `test_parse_ref_arg_callable_rejected` | `{"__callable__": ...}` → Error (không support) |

Tất cả là pure logic test, chạy < 1 giây, không cần network hay file system.

---

## CI Flow tổng thể sau fix

```
┌─ Rust Runtime CI ───────────────────────────────────────────┐
│                                                             │
│  Step 1: cargo test --lib          (8 Rust unit tests)      │
│          ↓                                                  │
│  Step 2: maturin develop --release (build Python module)    │
│          ↓                                                  │
│  Step 3: pytest tests/ -v          (136 Python tests)       │
│                                                             │
│  Total: 144 tests (8 Rust + 136 Python)                     │
└─────────────────────────────────────────────────────────────┘
```
