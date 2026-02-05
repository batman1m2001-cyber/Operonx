# Hush Trace Viewer - VS Code Extension Installer (Windows PowerShell)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VsixFile = Join-Path $ScriptDir "hush-vscode-traceview-0.1.0.vsix"

# Install dependencies
Write-Host "Installing dependencies..."
Push-Location $ScriptDir
npm install
if ($LASTEXITCODE -ne 0) {
    Write-Host "Failed to install dependencies!" -ForegroundColor Red
    Pop-Location
    exit 1
}

# Build the extension
Write-Host "Building extension..."
npm run package
if ($LASTEXITCODE -ne 0) {
    Write-Host "Build failed!" -ForegroundColor Red
    Pop-Location
    exit 1
}
Pop-Location

# Check if vsix exists
if (-not (Test-Path $VsixFile)) {
    Write-Host "Error: $VsixFile not found after build!" -ForegroundColor Red
    exit 1
}

# Check if code command exists
$codeCmd = Get-Command code -ErrorAction SilentlyContinue
if (-not $codeCmd) {
    Write-Host "Error: 'code' command not found!" -ForegroundColor Red
    Write-Host "Make sure VS Code is installed and added to PATH."
    Write-Host ""
    Write-Host "Reinstall VS Code and check 'Add to PATH' option,"
    Write-Host "or open VS Code and run:"
    Write-Host "  Ctrl+Shift+P -> 'Shell Command: Install code command in PATH'"
    exit 1
}

# Uninstall old version if exists
Write-Host "Removing old version (if any)..."
code --uninstall-extension hush.hush-vscode-traceview 2>$null

# Install new version
Write-Host "Installing Hush Trace Viewer..."
code --install-extension $VsixFile

if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "Installation complete!" -ForegroundColor Green
    Write-Host ""
    Write-Host "Reload VS Code and use:"
    Write-Host "  Ctrl+Shift+P -> 'Hush: Open Traces'"
} else {
    Write-Host "Installation failed!" -ForegroundColor Red
    exit 1
}
