@echo off
cd /d "%~dp0"
set PY_EXE=python
if exist "D:\SoftWare\Conda\python.exe" set PY_EXE=D:\SoftWare\Conda\python.exe
echo Starting Gate AutoTest Python HTTP Backend on port 3100...
"%PY_EXE%" main.py
pause
