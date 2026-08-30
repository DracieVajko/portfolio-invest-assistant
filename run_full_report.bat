@echo off
setlocal

cd /d "%~dp0"

if not exist logs mkdir logs

echo.
echo ======================================================
echo Portfolio AI Assistant
echo ======================================================
echo.

python portfolio_ai_assistant.py

echo.
echo ======================================================
echo Finished
echo Exit code: %ERRORLEVEL%
echo ======================================================
echo.

pause