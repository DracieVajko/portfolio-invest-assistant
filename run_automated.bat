@echo off
cd /d "%~dp0"

if not exist logs mkdir logs

python portfolio_ai_assistant.py --config portfolio_config.json --no-interactive > logs\latest_run.log 2>&1
exit /b %ERRORLEVEL%
