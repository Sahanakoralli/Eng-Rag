# Setup for Windows (PowerShell). Usage: .\scripts\setup.ps1 [-NoParsing] [-Gpu]
param([switch]$NoParsing, [switch]$Gpu)
Set-Location (Join-Path $PSScriptRoot "..")

function Fail($msg) { Write-Host "`nERROR: $msg" -ForegroundColor Red; exit 1 }
function Run($desc) {
    if ($LASTEXITCODE -ne 0) { Fail "$desc failed (exit code $LASTEXITCODE). See the messages above." }
}

# --- Find a real Python (skip the Microsoft Store stub) ---
$py = $null
foreach ($cand in @($env:PYTHON, "py", "python", "python3")) {
    if (-not $cand) { continue }
    $cmd = Get-Command $cand -ErrorAction SilentlyContinue
    if (-not $cmd) { continue }
    if ($cmd.Source -like "*WindowsApps*") { continue }   # Store stub, not real Python
    & $cand -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" 2>$null
    if ($LASTEXITCODE -eq 0) { $py = $cand; break }
}
if (-not $py) {
    Fail "No Python 3.10+ found. Install it from https://www.python.org/downloads/ and tick 'Add python.exe to PATH', then open a NEW PowerShell window."
}
Write-Host "Using Python: $py  ($(& $py --version))"

# --- Create venv (remove a broken one first) ---
if (Test-Path .venv) { Write-Host "==> Removing old .venv"; Remove-Item -Recurse -Force .venv }
Write-Host "==> Creating virtual environment (.venv)"
& $py -m venv .venv
Run "Creating the virtual environment"

$python = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) { Fail "venv was not created (.venv\Scripts\python.exe missing)." }

& $python -m pip install --quiet --upgrade pip
Run "Upgrading pip"

Write-Host "==> Installing core dependencies"
& $python -m pip install -r requirements.txt
Run "Installing requirements.txt"
& $python -m pip install -e . --quiet
Run "Installing the project package"

if (-not $NoParsing) {
    if (-not $Gpu) {
        Write-Host "==> Installing CPU-only PyTorch (small download, no CUDA)"
        & $python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
        Run "Installing CPU PyTorch"
    }
    Write-Host "==> Installing Docling (document parser)"
    & $python -m pip install -r requirements-parsing.txt
    Run "Installing Docling"
}

if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
    Write-Host "==> Created .env (add your ANTHROPIC_API_KEY)"
}

Write-Host "==> Verifying setup"
& $python scripts\verify_setup.py --models

Write-Host "`nDone. Activate with:  .\.venv\Scripts\Activate.ps1" -ForegroundColor Green
