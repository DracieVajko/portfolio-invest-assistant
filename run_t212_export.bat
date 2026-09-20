@echo off
setlocal

cd /d "%~dp0"

if not exist reports mkdir reports
if not exist logs mkdir logs

echo Exporting Trading212 portfolio to JSON...

REM Pin Python 3.12 (verified env with all dependencies).
py -3.12 --version >nul 2>nul
if %errorlevel%==0 (
    set "PYCMD=py -3.12"
) else (
    set "PYCMD=python"
)

REM Self-heal missing dependencies on double-click runs.
%PYCMD% -c "import numpy, requests, yfinance, pandas" >nul 2>nul
if errorlevel 1 (
    echo [SETUP] Installing missing dependencies...
    %PYCMD% -m pip install -r requirements.txt
    echo.
)

%PYCMD% -m trading212.integration --export reports\t212_portfolio.json --config api.env

if errorlevel 1 (
    echo [ERROR] Export failed.
    pause
    exit /b 1
)

echo.
echo Exported: reports\t212_portfolio.json
pause
