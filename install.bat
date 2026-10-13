@echo off
title Portfolio Analysis - Install
set ROOT=%~dp0
cd /d "%ROOT%"
echo ==========================================
echo Portfolio AI Assistant - Python 3.12 Setup
echo ==========================================
echo.

REM Check Python 3.12
py -3.12 --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python 3.12 not found.
    echo Please install Python 3.12.
    pause
    exit /b 1
)

echo Python version:
py -3.12 --version

echo.
echo Checking pip...
py -3.12 -m pip --version
if errorlevel 1 (
    echo ERROR: pip is not available for Python 3.12.
    pause
    exit /b 1
)

echo.
echo Upgrading pip...
py -3.12 -m pip install --upgrade pip

echo.
echo Installing requirements from requirements.txt...
py -3.12 -m pip install -r requirements.txt

if errorlevel 1 (
    echo.
    echo ERROR: Requirements installation failed.
    pause
    exit /b 1
)

echo.
echo Verifying core installations...
py -3.12 -c "import yfinance; import pandas; import scipy; import feedparser; import finvizfinance; import numpy; import matplotlib; print('Core packages OK!')"

echo.
echo Checking pinned versions (pandas 2.2.x + numpy 1.26.x + scipy 1.13.x)...
py -3.12 -c "import pandas, numpy, scipy, sys; ok = pandas.__version__.startswith('2.2.') and numpy.__version__.startswith('1.26.') and scipy.__version__.startswith('1.13.'); print(f'pandas={pandas.__version__} numpy={numpy.__version__} scipy={scipy.__version__}'); sys.exit(0 if ok else 1)"
if errorlevel 1 (
    echo.
    echo ERROR: Version mismatch. Expected pandas 2.2.x + numpy 1.26.x + scipy 1.13.x.
    echo If you had pandas 3.x before, run: py -3.12 -m pip install --force-reinstall -r requirements.txt
    pause
    exit /b 1
)

echo.
echo Running pip check (broken dependencies)...
py -3.12 -m pip check
if errorlevel 1 (
    echo.
    echo ERROR: pip check found broken requirements (see above).
    pause
    exit /b 1
)

echo.
echo Checking pandas_ta...
py -3.12 -c "import importlib.util; print('pandas_ta OK (reference only)') if importlib.util.find_spec('pandas_ta') else print('pandas_ta not available - expected, manual indicator engine is canonical (harmless)')"

echo.
echo Installing Playwright Chromium browser (once per machine)...
py -3.12 -m playwright install chromium
if errorlevel 1 (
    echo.
    echo WARNING: Playwright browser install failed. The TradingView sweep
    echo will degrade gracefully to Yahoo/RSS data. Re-run later:
    echo   py -3.12 -m playwright install chromium
) else (
    echo Playwright Chromium OK.
)

echo.
echo Verifying Playwright import...
py -3.12 -c "import playwright; print('playwright pip package OK')"

echo.
echo ==========================================
echo Installation complete.
echo Python 3.12 is being used.
echo ==========================================
pause
