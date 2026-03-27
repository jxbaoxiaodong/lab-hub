@echo off
chcp 65001 >nul
title Lab Client - 标准检测工具
echo ==========================================
echo   Lab Client - 标准检测工具
echo ==========================================
echo.

REM 检查Python版本
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 Python，请先安装 Python 3.8+
    echo.
    echo 下载地址: https://www.python.org/downloads/
    echo 或使用国内镜像: https://mirrors.huaweicloud.com/python/
    pause
    exit /b 1
)

REM 检查Python版本是否 >= 3.8
for /f "tokens=2 delims= " %%i in ('python --version 2^>nul') do set PYTHON_VERSION=%%i
for /f "tokens=1,2 delims=." %%a in ("%PYTHON_VERSION%") do (
    if %%a LSS 3 (
        echo [错误] Python版本过低: %PYTHON_VERSION%，需要 Python 3.8+
        pause
        exit /b 1
    )
    if %%a EQU 3 if %%b LSS 8 (
        echo [错误] Python版本过低: %PYTHON_VERSION%，需要 Python 3.8+
        pause
        exit /b 1
    )
)

REM 检查依赖
if not exist "venv" (
    echo [1/3] 创建虚拟环境...
    python -m venv venv
)

echo [2/3] 激活虚拟环境...
call venv\Scripts\activate.bat

REM 安装依赖（使用国内镜像源，带重试）
echo [3/3] 检查依赖（使用国内镜像源）...
set PIP_MIRRORS=0
:install_deps
if %PIP_MIRRORS%==0 (
    echo 使用清华镜像源...
    pip install -q -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn
) else if %PIP_MIRRORS%==1 (
    echo 清华源失败，尝试阿里云镜像...
    pip install -q -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com
) else if %PIP_MIRRORS%==2 (
    echo 阿里云失败，尝试中科大镜像...
    pip install -q -r requirements.txt -i https://pypi.mirrors.ustc.edu.cn/simple/ --trusted-host pypi.mirrors.ustc.edu.cn
) else (
    echo 所有镜像源失败，尝试官方源...
    pip install -q -r requirements.txt
)

if errorlevel 1 (
    set /a PIP_MIRRORS+=1
    if %PIP_MIRRORS% LSS 4 (
        echo 安装失败，尝试下一个镜像源...
        goto install_deps
    ) else (
        echo [错误] 依赖安装失败，请检查网络连接
        pause
        exit /b 1
    )
)

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
