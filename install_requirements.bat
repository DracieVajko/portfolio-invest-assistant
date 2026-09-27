@echo off
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
py -3.12 -c "import yfinance; import pandas; import scipy; import feedparser; import finvizfinance; import numpy; print('Core packages OK!')"

echo.
echo Checking pandas_ta...
py -3.12 -c "import importlib.util; print('pandas_ta OK') if importlib.util.find_spec('pandas_ta') else print('pandas_ta not available - using manual indicator fallback (built-in)')"

echo.
echo ==========================================
echo Installation complete.
echo Python 3.12 is being used.
echo ==========================================
pause