#!/bin/bash
# Hush Trace Viewer - VS Code Extension Installer (Linux/Mac)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VSIX_FILE="$SCRIPT_DIR/hush-vscode-traceview-0.1.0.vsix"

# Install dependencies
echo "Installing dependencies..."
cd "$SCRIPT_DIR"
npm install
if [ $? -ne 0 ]; then
    echo "Failed to install dependencies!"
    exit 1
fi

# Build the extension
echo "Building extension..."
npm run package
if [ $? -ne 0 ]; then
    echo "Build failed!"
    exit 1
fi

# Check if vsix exists
if [ ! -f "$VSIX_FILE" ]; then
    echo "Error: $VSIX_FILE not found after build!"
    exit 1
fi

# Check if code command exists
if ! command -v code &> /dev/null; then
    echo "Error: 'code' command not found!"
    echo "Make sure VS Code is installed and 'code' is in your PATH."
    echo ""
    echo "Open VS Code and run:"
    echo "  Ctrl+Shift+P -> 'Shell Command: Install code command in PATH'"
    exit 1
fi

# Uninstall old version if exists
echo "Removing old version (if any)..."
code --uninstall-extension hush.hush-vscode-traceview 2>/dev/null || true

# Install new version
echo "Installing Hush Trace Viewer..."
code --install-extension "$VSIX_FILE"

if [ $? -eq 0 ]; then
    echo ""
    echo "Installation complete!"
    echo ""
    echo "Reload VS Code and use:"
    echo "  Ctrl+Shift+P -> 'Hush: Open Traces'"
else
    echo "Installation failed!"
    exit 1
fi
