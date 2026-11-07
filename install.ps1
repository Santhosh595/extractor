# One-line installer: checks Git + Python, clones extractor, runs setup.
# Read me first — this whole file is ~60 lines and does only the steps below.
# No AI, no telemetry, no URLs except the GitHub clone.
$ErrorActionPreference = 'Stop'

Write-Host "=== Extractor setup ===" -ForegroundColor Cyan

# --- 1. Git ---
$git = Get-Command git -ErrorAction SilentlyContinue
if (-not $git) {
    Write-Host "Git not found. Installing via winget..."
    winget install --id Git.Git -e --accept-source-agreements --accept-package-agreements
    $git = Get-Command git -ErrorAction SilentlyContinue
    if (-not $git) { Write-Host "Git install failed. Install from https://git-scm.com and re-run." -ForegroundColor Red; exit 1 }
} else { Write-Host "Git found: $($git.Source)" }

# --- 2. Python ---
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) {
    Write-Host "Python not found. Installing via winget..."
    winget install --id Python.Python.3.12 -e --accept-source-agreements --accept-package-agreements
    $py = Get-Command python -ErrorAction SilentlyContinue
    if (-not $py) { Write-Host "Python install failed. Install from https://python.org and re-run." -ForegroundColor Red; exit 1 }
} else { Write-Host "Python found: $($py.Source)" }

# --- 3. Clone ---
$dest = "$env:USERPROFILE\extractor"
if (Test-Path $dest) {
    Write-Host "Already cloned at $dest — updating..."
    Set-Location $dest; git pull
} else {
    git clone https://github.com/Santhosh595/extractor.git $dest
    if ($LASTEXITCODE -ne 0) { Write-Host "Clone failed (network?). Check connection and re-run." -ForegroundColor Red; exit 1 }
    Set-Location $dest
}

# --- 4. Setup (asks for Telegram token + chat id + master password, once) ---
Write-Host "`nNext: enter your Telegram bot token and chat id (saved encrypted, password-protected)." -ForegroundColor Yellow
python extractor.py setup

Write-Host "`nDone. Start watching with:" -ForegroundColor Cyan
Write-Host "  python extractor.py run          # every 10 minutes, forever"
Write-Host "  python extractor.py run --once   # single check, exit"
