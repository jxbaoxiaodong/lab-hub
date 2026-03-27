@echo off
chcp 65001 >nul
title Lab Client - 标准检测工具
echo ==========================================
echo   Lab Client - 标准检测工具
echo ==========================================
echo.

REM 检查Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 Python，请先安装 Python 3.8+
    pause
    exit /b 1
)

REM 检查依赖
if not exist "venv" (
    echo [1/3] 创建虚拟环境...
    python -m venv venv
)

echo [2/3] 激活虚拟环境...
call venv\Scripts\activate.bat

REM 安装依赖
echo [3/3] 检查依赖...
pip install -q -r requirements.txt

echo.
echo ==========================================
echo   启动客户端...
echo ==========================================
echo.
echo 浏览器将自动打开，请稍候...
echo.

REM 启动应用
python app.py

pause
