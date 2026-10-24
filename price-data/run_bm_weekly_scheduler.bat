@echo off
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" src\bm_weekly_scheduler.py %*
) else (
  python src\bm_weekly_scheduler.py %*
)

endlocal
