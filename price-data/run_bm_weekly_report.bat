@echo off
setlocal
cd /d "%~dp0"
F:\python\python.exe src\bm_weekly_report.py %*
endlocal
