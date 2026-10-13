#!/usr/bin/env bash
# Portfolio AI Assistant - POSIX setup (Python 3.12).
# Mirror of install.bat; requirements.txt stays the single
# source of truth for dependencies.
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-python3.12}"
command -v "$PY" >/dev/null 2>&1 || { echo "ERROR: $PY not found."; exit 1; }

echo "Python version:"
"$PY" --version

echo "Checking pip..."
"$PY" -m pip --version

echo "Upgrading pip..."
"$PY" -m pip install --upgrade pip

echo "Installing requirements from requirements.txt..."
"$PY" -m pip install -r requirements.txt

echo "Verifying core installations..."
"$PY" -c "import yfinance, pandas, scipy, feedparser, finvizfinance, numpy, matplotlib; print('Core packages OK!')"

echo "Checking pinned versions (pandas 2.2.x + numpy 1.26.x + scipy 1.13.x)..."
"$PY" -c "import pandas, numpy, scipy, sys; ok = pandas.__version__.startswith('2.2.') and numpy.__version__.startswith('1.26.') and scipy.__version__.startswith('1.13.'); print(f'pandas={pandas.__version__} numpy={numpy.__version__} scipy={scipy.__version__}'); sys.exit(0 if ok else 1)"

echo "Running pip check (broken dependencies)..."
"$PY" -m pip check

echo "Checking pandas_ta..."
"$PY" -c "import importlib.util; print('pandas_ta OK (reference only)') if importlib.util.find_spec('pandas_ta') else print('pandas_ta not available - expected, manual indicator engine is canonical (harmless)')"

echo "Installing Playwright Chromium browser (once per machine)..."
if "$PY" -m playwright install chromium; then
  echo "Playwright Chromium OK."
else
  echo "WARNING: Playwright browser install failed. The TradingView sweep"
  echo "will degrade gracefully to Yahoo/RSS data. Re-run later:"
  echo "  $PY -m playwright install chromium"
fi

echo "Verifying Playwright import..."
"$PY" -c "import playwright; print('playwright pip package OK')"

echo "=========================================="
echo "Installation complete."
echo "Python 3.12 is being used."
echo "=========================================="
