@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo ============================================================
echo  出版业务 AI 系统 - 印制参数校验与成本测算
echo  项目目录：%~dp0
echo ============================================================

rem 依赖：本机已通过 `python -m pip install -r requirements.txt` 安装；
rem 若使用离线部署包，可改为 `pip install -r requirements.txt --target vendor`
rem 并在启动前执行 `set PYTHONPATH=%~dp0vendor`

echo [1/4] 环境预检 ...
python scripts\preflight.py
if errorlevel 1 (
    echo.
    echo [错误] 环境预检未通过。若提示缺少依赖，请执行：
    echo        python -m pip install -r requirements.txt
    echo        或使用离线方式安装到 vendor 目录后重试。
    pause
    exit /b 1
)

echo.
echo [2/4] 初始化演示数据（幂等，可重复执行）...
python seed\seed_demo.py
if errorlevel 1 (
    echo.
    echo [错误] 演示数据初始化失败。
    pause
    exit /b 1
)

echo.
echo [3/4] 启动服务：http://127.0.0.1:8000  （Ctrl+C 停止）
echo       演示账号：operator/123456（业务操作员 张伟） reviewer/123456（审核员 李静）
echo.
echo [4/4] 正在启动 uvicorn ...
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1

echo.
echo 服务已停止。
pause
