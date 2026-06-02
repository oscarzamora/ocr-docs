Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# INTERNAL USE ONLY: prepares the session prompt from local template.
$repoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$templatePath = Join-Path $repoRoot "SESSION_START.md"

# Startup hygiene: remove leftover OCR temp folder from previous sessions.
$downloadsOcrTmp = "C:\Users\ozamo\OneDrive\Documents\__downloads__\_ocr_tmp"
if (Test-Path $downloadsOcrTmp) {
    cmd /c "rmdir /s /q \"$downloadsOcrTmp\"" | Out-Null
    if (-not (Test-Path $downloadsOcrTmp)) {
        Write-Host "Removed leftover temp folder: $downloadsOcrTmp" -ForegroundColor DarkGray
    }
}

if (-not (Test-Path $templatePath)) {
    Write-Error "SESSION_START.md not found at: $templatePath"
}

$templateText = Get-Content $templatePath -Raw
$templateText | Set-Clipboard

Write-Host "INTERNAL USE ONLY SESSION LAUNCHER" -ForegroundColor Yellow
Write-Host "Template copied to clipboard:" -ForegroundColor Green
Write-Host "  $templatePath"
Write-Host ""
Write-Host "Next:" -ForegroundColor Cyan
Write-Host "1. Paste into the new chat as first message."
Write-Host "2. Fill only changing fields (source folder, run mode, handoff deltas)."
Write-Host "3. Run the session."

$codeCmd = Get-Command code -ErrorAction SilentlyContinue
if ($null -ne $codeCmd) {
    & $codeCmd.Source -g "$templatePath:1"
    Write-Host "Opened template in VS Code." -ForegroundColor DarkGray
}
