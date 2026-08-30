@echo off
title Portfolio Analysis - Full Report
cd /d "%~dp0"

echo Running full portfolio analysis...
python portfolio_ai_assistant.py --config portfolio_config.json

if errorlevel 1 (
    echo.
    echo [ERROR] Portfolio analysis failed. Check the messages above.
    pause
    exit /b 1
)

echo.
echo Report saved to: reports\portfolio_analysis.md
echo Data saved to: reports\portfolio_analysis.json
pause
