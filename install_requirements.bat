@echo off
echo Installing Python requirements...
echo.

REM Check if pip is available
py -3.12 -m pip --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python 3.12 or pip not found in PATH
    echo Please install Python 3.12 from https://python.org and ensure "Add to PATH" is checked
    pause
    exit /b 1
)

echo Upgrading pip...
py -3.12 -m pip install --upgrade pip

echo.
echo Installing requirements from requirements.txt...
py -3.12 -m pip install -r requirements.txt

echo.
echo NOTE: pandas-ta requires numba which doesn't support Python 3.14+ yet.
echo The system works WITHOUT pandas-ta (manual indicators are built-in).
echo Skipping pandas-ta/numba installation for Python 3.14+ compatibility.
echo.

echo Verifying core installations...
py -3.12 -c "import yfinance; import pandas; import scipy; import feedparser; import finvizfinance; import numpy; import scipy; print('Core packages OK!')"

echo.
echo Attempting to import pandas_ta (optional, may fail on Python 3.14+)...
py -3.12 -c "try: import pandas_ta; print('pandas_ta OK (numba available)') except ImportError: print('pandas_ta not available - using manual indicator fallback (built-in)')"

echo.
echo All done! System ready to run.
pause