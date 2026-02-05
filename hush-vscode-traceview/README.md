# Hush Trace Viewer

VS Code extension để xem và phân tích traces từ Hush workflows.

## Tính năng

- Xem danh sách tất cả workflow traces
- Tree view hiển thị cấu trúc phân cấp của nodes
- Chi tiết từng node: duration, tokens, cost, input/output
- JSON syntax highlighting cho input/output data
- Refresh và clear traces trực tiếp từ VS Code

## Cài đặt

### Từ VSIX file

**Linux/Mac:**
```bash
cd hush-vscode-traceview
./install.sh
```

**Windows (PowerShell):**
```powershell
cd hush-vscode-traceview
.\install.ps1
```

### Build từ source

```bash
cd hush-vscode-traceview
npm install
npm run package
./install.sh  # hoặc install.ps1 trên Windows
```

## Sử dụng

1. Mở VS Code
2. Nhấn `Ctrl+Shift+P` (hoặc `Cmd+Shift+P` trên Mac)
3. Gõ `Hush: Open Traces`
4. Chọn một trace để xem chi tiết

## Commands

| Command | Mô tả |
|---------|-------|
| `Hush: Open Traces` | Mở panel xem traces |
| `Hush: Refresh Traces` | Refresh danh sách traces |
| `Hush: Clear Traces` | Xóa tất cả traces |

## Database

Extension đọc traces từ SQLite database tại `~/.hush/traces.db`. Database này được tự động tạo bởi `LocalTracer` trong hush-core khi chạy workflows.

## Screenshots

### Trace List
Danh sách workflows với thông tin: tên, thời gian, duration, tokens, cost.

### Trace Detail
- **Tree panel (trái)**: Cấu trúc phân cấp của nodes
- **Detail panel (phải)**: Thông tin chi tiết của node được chọn

## Development

```bash
# Cài dependencies
npm install

# Build
npm run compile

# Watch mode
npm run watch

# Package thành VSIX
npm run package
```

## Yêu cầu

- VS Code 1.85.0+
- Node.js 18+

## License

MIT
