@echo off
cd /d "%~dp0"
chcp 65001 >nul
REM 测试模式运行批处理文件（只爬取少量数据用于测试）

echo ========================================
echo Klook租车数据爬虫 - 测试模式
echo ========================================
REM 检查Python是否安装
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到Python，请先安装Python 3.7+
    timeout /t 3 >nul
    exit /b 1
)

REM 检查依赖是否安装
python -c "import playwright" >nul 2>&1
if errorlevel 1 (
    echo [提示] 正在安装依赖...
    pip install -r requirements.txt
    if errorlevel 1 (
        echo [错误] 依赖安装失败
        timeout /t 3 >nul
        exit /b 1
    )
)

echo [信息] 开始运行测试模式...
echo [信息] 使用 spiderv3-test.py 执行爬虫...
echo.

REM 运行 spiderv3-test.py
python src\spiderv3-test.py

if errorlevel 1 (
    echo.
    echo [错误] 程序运行出错
    timeout /t 3 >nul
    exit /b 1
)

echo.
echo [完成] 
timeout /t 2 >nul

