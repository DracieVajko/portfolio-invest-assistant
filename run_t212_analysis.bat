@echo off
setlocal

cd /d "%~dp0"

if not exist logs mkdir logs

echo.
echo ======================================================
echo Trading212 Portfolio Analysis
echo ======================================================
echo.

REM Pin Python 3.12 (verified env with all dependencies).
REM Bare `python` on PATH can resolve to another install (3.13/3.14)
REM that lacks the project packages -> ModuleNotFoundError.
py -3.12 --version >nul 2>nul
if %errorlevel%==0 (
    set "PYCMD=py -3.12"
) else (
    set "PYCMD=python"
)

%PYCMD% -c "import sys; print(sys.executable)"
echo.

REM Self-heal missing dependencies on double-click runs.
%PYCMD% -c "import numpy, requests, yfinance, pandas" >nul 2>nul
if errorlevel 1 (
    echo [SETUP] Installing missing dependencies...
    %PYCMD% -m pip install -r requirements.txt
    echo.
)

REM -- health check --
echo [1/2] Health check...
%PYCMD% -m trading212.integration --health --config api.env
if errorlevel 1 (
    echo [ERROR] Trading212 connection failed.
    pause
    exit /b 1
)

echo.
echo [2/2] Running AI analysis (odysseus/Ollama)...
%PYCMD% -m trading212.integration --analyze --config api.env
if errorlevel 1 (
    echo [ERROR] AI analysis failed.
    pause
    exit /b 1
)

echo.
echo ======================================================
echo Finished
echo Exit code: %ERRORLEVEL%
echo ======================================================
echo.
