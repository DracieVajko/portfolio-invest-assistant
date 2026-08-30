@echo off
cd /d "%~dp0"

REM Task Scheduler variant: no pause, and Python owns logs\latest_run.log.
REM The selected FinR1/Gemma/GPT-OSS models must be loaded and server-enabled in LM Studio.
py -3.12 portfolio_ai_assistant.py --config portfolio_config.json --investment-engine --generate-full-report
exit /b %ERRORLEVEL%
