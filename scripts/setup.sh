#!/usr/bin/env bash
# Setup for macOS / Linux. Usage: bash scripts/setup.sh [--no-parsing] [--gpu]
set -euo pipefail
cd "$(dirname "$0")/.."

INSTALL_PARSING=1
CPU_TORCH=1
for arg in "$@"; do
  case $arg in
    --no-parsing) INSTALL_PARSING=0 ;;
    --gpu) CPU_TORCH=0 ;;
  esac
done

PY=${PYTHON:-python3}
$PY - << 'PYCHECK'
import sys
if sys.version_info < (3, 10):
    sys.exit(f"Python 3.10+ required, found {sys.version.split()[0]}")
print(f"Python {sys.version.split()[0]} OK")
PYCHECK

echo "==> Creating virtual environment (.venv)"
$PY -m venv .venv
. .venv/bin/activate
pip install --quiet --upgrade pip

echo "==> Installing core dependencies"
pip install -r requirements.txt
pip install -e . --quiet

if [ "$INSTALL_PARSING" = "1" ]; then
  if [ "$CPU_TORCH" = "1" ]; then
    echo "==> Installing CPU-only PyTorch (small download, no CUDA)"
    pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
  fi
  echo "==> Installing Docling (document parser)"
  pip install -r requirements-parsing.txt
fi

[ -f .env ] || { cp .env.example .env; echo "==> Created .env (add your ANTHROPIC_API_KEY)"; }

echo "==> Verifying setup"
python scripts/verify_setup.py --models

echo
echo "Done. Activate with:  source .venv/bin/activate"
