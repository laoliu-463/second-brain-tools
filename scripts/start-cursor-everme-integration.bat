@echo off
REM Cursor EverMe Hook集成启动脚本
REM 用于启动适配器服务并验证配置

echo ========================================
echo Cursor EverMe Hook集成启动
echo ========================================

echo.
echo 1. 检查适配器配置...
if exist "d:\第二大脑\studio\everme-everos-adapter\runtime\config.json" (
    echo [OK] 适配器配置文件存在
) else (
    echo [ERROR] 适配器配置文件不存在
    exit /b 1
)

echo.
echo 2. 检查适配器服务状态...
curl -s http://127.0.0.1:18100/healthz >nul 2>&1
if %errorlevel% equ 0 (
    echo [OK] 适配器服务运行中
) else (
    echo [INFO] 适配器服务未运行，需要启动
    echo [TODO] 手动启动适配器服务:
    echo   cd d:\第二大脑\studio\everme-everos-adapter
    echo   python -m everme_adapter --runtime-path runtime --config runtime/config.json
)

echo.
echo 3. 检查Cursor Hook配置...
if exist "C:\Users\caojianing\.cursor\hooks.json" (
    echo [OK] Cursor Hook配置文件存在
) else (
    echo [ERROR] Cursor Hook配置文件不存在
    exit /b 1
)

echo.
echo 4. 检查EverMe配置...
if exist "C:\Users\caojianing\.cursor\everme-config.json" (
    echo [OK] EverMe配置文件存在
) else (
    echo [ERROR] EverMe配置文件不存在
    exit /b 1
)

echo.
echo ========================================
echo 配置检查完成
echo ========================================
echo.
echo 下一步操作:
echo 1. 修改 hooks.json 中的 REPLACE_WITH_YOUR_TOKEN 为实际token
echo 2. 启动适配器服务（如果未运行）
echo 3. 在Cursor中测试Hook集成
echo.
pause