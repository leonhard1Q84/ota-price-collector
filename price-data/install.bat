@echo off
cd /d "%~dp0"
chcp 65001 >nul
echo ========================================
echo Klook租车数据爬虫 - 自动安装脚本
echo ========================================
echo.

REM 检查Python是否安装
echo [1/4] 检查Python安装...
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到Python，请先安装Python 3.8+
    echo.
    echo 请访问 https://www.python.org/downloads/ 下载并安装Python
    echo 安装时请务必勾选 "Add Python to PATH" 选项
    echo.
    timeout /t 10 >nul
    exit /b 1
) else (
    python --version
    echo [√] Python已安装
)
python -c "import sys; sys.exit(0 if not sys.version_info < (3, 8) else 1)" >nul 2>&1
if errorlevel 1 (
    echo [错误] 当前Python版本过低，请升级到Python 3.8+
    timeout /t 10 >nul
    exit /b 1
)
echo.

REM 升级pip
echo [2/4] 升级pip...
python -m pip install --upgrade pip -q
if errorlevel 1 (
    echo [警告] pip升级失败，继续安装依赖...
) else (
    echo [√] pip已升级
)
echo.

REM 安装Python依赖
echo [3/4] 安装Python依赖包（这可能需要几分钟）...
echo.
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo [错误] 依赖安装失败
    echo.
    echo 如果遇到网络问题，可以尝试使用国内镜像：
    echo pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
    echo.
    timeout /t 10 >nul
    exit /b 1
) else (
    echo.
    echo [√] Python依赖包安装完成
)
echo.

REM 安装Playwright浏览器驱动
echo [4/4] 安装Playwright浏览器驱动（这可能需要几分钟）...
echo.
python -m playwright install chromium
if errorlevel 1 (
    echo.
    echo [警告] Playwright浏览器驱动安装失败
    echo.
    echo 如果下载速度慢，可以设置环境变量后重试：
    echo set PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright
    echo python -m playwright install chromium
    echo.
    timeout /t 5 >nul
) else (
    echo.
    echo [√] Playwright浏览器驱动安装完成
)
echo.

echo ========================================
echo [完成] 安装完成！
echo ========================================
echo.
echo 现在可以运行 run_klook.bat 来启动爬虫
echo.
timeout /t 5 >nul

