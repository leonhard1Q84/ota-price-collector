@echo off
setlocal
cd /d "%~dp0"
if not defined FEISHU_APP_ID (echo FEISHU_APP_ID is required & exit /b 1)
if not defined FEISHU_APP_SECRET (echo FEISHU_APP_SECRET is required & exit /b 1)
F:\python\python.exe src\feishu_schedule.py --watch >> src\feishu_schedule_runtime\schedule.out.log 2>> src\feishu_schedule_runtime\schedule.err.log
endlocal
