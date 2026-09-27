@echo off
cd /d "%~dp0"

REM Task Scheduler variant: no pause, and Python owns logs\latest_run.log.
REM Usage: run_automated.bat [auto|public|locals]  (default: auto)
REM   auto   = full chain, locals first (LM Studio -> Pi -> public APIs)
REM   public = public APIs only (locals never touched)
REM   locals = local endpoints only + deterministic fallback
REM Models are stage-routed (decision=qwen3.8-9b-distill, finance=fin-o1-14b,
REM thinking=tongyi-30b-a3b, summary=google/gemma-4-12b) and auto-loaded on
REM demand by LM Studio.
set MODE=%1
if "%MODE%"=="" set MODE=auto
py -3.12 portfolio_ai_assistant.py --config portfolio_config.json --investment-engine --generate-full-report --execution-mode %MODE%
exit /b %ERRORLEVEL%
