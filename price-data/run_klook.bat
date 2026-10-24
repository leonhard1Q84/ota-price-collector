@echo off
cd /d "%~dp0"
chcp 65001 >nul
REM 运行批处理文件

echo ========================================
echo Klook租车数据爬虫
echo ========================================
echo.
echo.

REM 检查Python是否安装
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到Python，请先安装Python 3.8+
    timeout /t 3 >nul
    exit /b 1
)

python -c "import sys; sys.exit(0 if not sys.version_info < (3, 8) else 1)" >nul 2>&1
if errorlevel 1 (
    echo [错误] 当前Python版本过低，请升级到Python 3.8+
    timeout /t 3 >nul
    exit /b 1
)

REM 检查Playwright是否安装且版本不低于1.43
python -c "import re,sys; from importlib.metadata import version; v=tuple(map(int,re.findall(r'\d+',version('playwright'))[:2])); sys.exit(0 if not v < (1,43) else 1)" >nul 2>&1
if errorlevel 1 (
    echo [提示] 正在安装或升级依赖...
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [错误] 依赖安装失败
        timeout /t 3 >nul
        exit /b 1
    )
)

echo [信息] 开始运行测试模式...
echo [信息] 使用 spiderv3.py 执行爬虫...
echo.

REM 运行 spiderv3.py
pushd "%~dp0src"
python spiderv3.py
set "KLOOK_EXIT_CODE=%ERRORLEVEL%"
popd

if not "%KLOOK_EXIT_CODE%"=="0" (
    echo.
    echo [错误] 程序运行出错
    timeout /t 3 >nul
    exit /b 1
)

echo.
echo [完成] 
timeout /t 2 >nul

