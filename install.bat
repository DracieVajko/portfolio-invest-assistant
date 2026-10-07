@echo off
REM Portfolio AI Assistant - install entry point (Python 3.12).
REM Thin wrapper: the real logic lives in install_requirements.bat so that
REM requirements.txt always stays the single source of truth for dependencies.
set ROOT=%~dp0
cd /d "%ROOT%"
call "%ROOT%install_requirements.bat"
