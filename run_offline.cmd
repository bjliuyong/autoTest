@echo off
rem ===========================================================================
rem  Offline launcher: uses dependencies vendored in vendor\python (no pip).
rem  NOTE: keep this file pure ASCII - cmd.exe parses .cmd in the OEM codepage
rem        and non-ASCII comments can break the script.
rem
rem  Usage:
rem      run_offline.cmd                        run tests + failure analysis
rem      run_offline.cmd --no-dsh               tests + reports only (no LLM)
rem      run_offline.cmd --analyze-only         drain the existing failure queue
rem      run_offline.cmd -c config.selftest.yaml   use another config file
rem      run_offline.cmd --log-level DEBUG      verbose
rem
rem  Optional env:
rem      set PYTHON_EXE=D:\Python311\python.exe to pick an interpreter
rem      set DEEPSEEK_API_KEY=...               instead of a prepared .dsh home
rem ===========================================================================
setlocal
chcp 65001 >nul 2>&1
set "HERE=%~dp0"
if "%PYTHON_EXE%"=="" set "PYTHON_EXE=python"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PYTHONPATH=%HERE%vendor\python;%PYTHONPATH%"

rem If the caller passed -c / --config, respect it instead of forcing our default.
set "HASCFG="
for %%a in (%*) do (
  if /i "%%~a"=="-c" set "HASCFG=1"
  if /i "%%~a"=="--config" set "HASCFG=1"
)

if defined HASCFG (
  "%PYTHON_EXE%" "%HERE%run_autotest.py" %*
) else (
  "%PYTHON_EXE%" "%HERE%run_autotest.py" -c "%HERE%config.offline.yaml" %*
)
set "RC=%ERRORLEVEL%"
endlocal & exit /b %RC%
