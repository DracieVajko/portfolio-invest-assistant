@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

echo ==========================================
echo Portfolio AI Assistant - Cleanup Script
echo ==========================================
echo.

set ROOT=%~dp0
cd /d "%ROOT%"

REM ==========================================
REM Configuration
REM ==========================================
set KEEP_LOGS_DAYS=30
set KEEP_REPORTS_DAYS=30
set KEEP_DEBUG_RUNS=14
set KEEP_AI_CONTEXT_DAYS=30

echo Cleaning up files older than %KEEP_LOGS_DAYS% days (logs), %KEEP_REPORTS_DAYS% days (reports)
echo Keeping last %KEEP_DEBUG_RUNS% debug runs
echo.

REM ==========================================
REM 1. Clean logs older than KEEP_LOGS_DAYS
REM ==========================================
echo [1/5] Cleaning logs older than %KEEP_LOGS_DAYS% days...
if exist "logs" (
    forfiles /p "logs" /s /m *.log /d -%KEEP_LOGS_DAYS% /c "cmd /c del @path" 2>nul
    forfiles /p "logs" /s /m *.txt /d -%KEEP_LOGS_DAYS% /c "cmd /c del @path" 2>nul
    echo Logs cleaned.
) else (
    echo No logs folder found.
)

REM ==========================================
REM 2. Clean reports older than KEEP_REPORTS_DAYS (but keep summary/latest)
REM ==========================================
echo [2/5] Cleaning reports older than %KEEP_REPORTS_DAYS% days...
if exist "reports" (
    REM Clean decision briefs in archive
    if exist "reports\archive" (
        forfiles /p "reports\archive" /m *.md /d -%KEEP_REPORTS_DAYS% /c "cmd /c del @path" 2>nul
        forfiles /p "reports\archive" /m *.json /d -%KEEP_REPORTS_DAYS% /c "cmd /c del @path" 2>nul
    )
    REM Clean old raw_endpoint_dump files in reports root
    forfiles /p "reports" /m raw_endpoint_dump_*.json /d -%KEEP_REPORTS_DAYS% /c "cmd /c del @path" 2>nul
    REM Clean old portfolio_analysis.json in archive
    if exist "reports\archive" (
        forfiles /p "reports\archive" /m portfolio_analysis_*.json /d -%KEEP_REPORTS_DAYS% /c "cmd /c del @path" 2>nul
    )
    echo Reports cleaned.
) else (
    echo No reports folder found.
)

REM ==========================================
REM 3. Prune debug folders (keep last KEEP_DEBUG_RUNS runs, and by date)
REM ==========================================
echo [3/5] Pruning debug folders (keep last %KEEP_DEBUG_RUNS% runs, max %KEEP_LOGS_DAYS% days)...
if exist "reports\debug" (
    REM Get all debug folders with their creation dates
    set COUNT=0
    for /f "tokens=*" %%d in ('dir /b /ad /o-d "reports\debug" 2^>nul') do (
        set /a COUNT+=1
        if !COUNT! GTR %KEEP_DEBUG_RUNS% (
            echo Pruning old debug run: %%d
            rmdir /s /q "reports\debug\%%d" 2>nul
        )
    )
    REM Also remove folders older than KEEP_LOGS_DAYS days
    forfiles /p "reports\debug" /s /d -%KEEP_LOGS_DAYS% /c "cmd /c if @isdir==TRUE rmdir /s /q @path" 2>nul
    echo Debug folders pruned.
) else (
    echo No debug folder found.
)

REM ==========================================
REM 4. Clean AI context older than KEEP_AI_CONTEXT_DAYS
REM ==========================================
echo [4/5] Cleaning AI context older than %KEEP_AI_CONTEXT_DAYS% days...
if exist "reports\ai_context" (
    forfiles /p "reports\ai_context" /m *.md /d -%KEEP_AI_CONTEXT_DAYS% /c "cmd /c del @path" 2>nul
    forfiles /p "reports\ai_context" /m *.json /d -%KEEP_AI_CONTEXT_DAYS% /c "cmd /c del @path" 2>nul
    echo AI context cleaned.
) else (
    echo No ai_context folder found.
)

REM ==========================================
REM 5. Clean Python cache and temp files
REM ==========================================
echo [5/5] Cleaning Python cache and temp files...
for /r %%d in (__pycache__) do @if exist "%%d" rmdir /s /q "%%d" 2>nul
for /r %%f in (*.pyc) do @if exist "%%f" del /q "%%f" 2>nul
for /r %%f in (*.pyo) do @if exist "%%f" del /q "%%f" 2>nul
if exist ".pytest_cache" rmdir /s /q ".pytest_cache" 2>nul
if exist ".mypy_cache" rmdir /s /q ".mypy_cache" 2>nul
if exist ".coverage" del /q ".coverage" 2>nul
if exist "*.tmp" del /q *.tmp 2>nul
if exist "*.temp" del /q *.temp 2>nul
echo Temp files cleaned.

echo.
echo ==========================================
echo Cleanup complete!
echo ==========================================
echo.
echo Summary of what was cleaned:
echo   - Logs older than %KEEP_LOGS_DAYS% days
echo   - Reports older than %KEEP_REPORTS_DAYS% days
echo   - Debug runs beyond last %KEEP_DEBUG_RUNS% (and older than %KEEP_LOGS_DAYS% days)
echo   - AI context older than %KEEP_AI_CONTEXT_DAYS% days
echo   - Python cache (__pycache__, *.pyc, .pytest_cache, .mypy_cache)
echo.
echo To customize retention, edit the variables at the top of this script.
echo.
pause