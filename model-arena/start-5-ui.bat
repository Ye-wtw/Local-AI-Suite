@echo off
chcp 936 > nul
cd /d "%~dp0"
setlocal enabledelayedexpansion

echo ==================================================
echo   model-arena 界面服务
echo.
echo   这个窗口就是服务本身, 请不要关闭它。
echo   关闭窗口 = 停止服务。
echo ==================================================
echo.

rem 端口不写死: 旧会话残留的进程可能占着端口却查不到,
rem 那时所有请求都由旧代码应答, 表现为"改了页面却不更新"。
rem --port 0 让程序自己找第一个空闲端口。
rem --open-ui 让程序在端口确定后自己开浏览器 (bat 拿不到那个端口)。
echo   正在启动服务, 端口自动选择, 浏览器会自动打开...
echo.

python runner\arena_api.py --port 0 --open-ui

echo.
echo   服务已退出 (代码 %errorlevel%)。
echo   若这是意外退出, 看上面 python 的报错。
pause
