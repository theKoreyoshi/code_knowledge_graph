@echo off
rem Run code-kg from any directory:  ckg.cmd build C:\path\to\project
setlocal
set "TOOL=%~dp0"
if exist "%TOOL%.venv\Scripts\python.exe" (
  set "PY=%TOOL%.venv\Scripts\python.exe"
) else (
  set "PY=python"
)
set "PYTHONPATH=%TOOL%;%PYTHONPATH%"
"%PY%" -m ckg %*
