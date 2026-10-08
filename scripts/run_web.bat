@echo off
rem 启动灯塔 OSINT 前端工作台（Vite dev server）。
rem 需要另一个终端先跑 scripts\run_api.bat，否则页面框架能开但没有数据。
setlocal
cd /d "%~dp0..\web"
if not exist node_modules (
  echo [灯塔] 首次运行，安装依赖...
  call npm install || exit /b 1
)
echo [灯塔] 前端 http://localhost:5173
call npm run dev
exit /b %ERRORLEVEL%