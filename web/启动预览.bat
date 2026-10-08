@echo off
chcp 65001 >nul
title 灯塔 OSINT 前端预览

echo ========================================
echo   灯塔 OSINT - 前端预览服务
echo ========================================
echo.

cd /d "D:\TVbox\BeaconOSINT\web\dist"

echo [1/2] 启动静态服务 http://127.0.0.1:8080/
start /b python -m http.server 8080 --bind 0.0.0.0

echo [2/2] 打开 Edge 浏览器
timeout /t 2 /nobreak >nul
start "" "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" "http://127.0.0.1:8080/"

echo.
echo 服务已启动，浏览器已打开。
echo 地址: http://127.0.0.1:8080/
echo.
echo 关闭此窗口不会停止服务。
echo 要停止服务: 关闭 Python 进程即可。
echo.
pause
