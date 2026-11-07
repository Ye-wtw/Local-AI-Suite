@echo off
chcp 936 > nul
cd /d "%~dp0"
echo ==================================================
echo   本地模型评测
echo ==================================================
echo.
echo 请先确认:
echo   1. 模型服务已启动 (默认 http://127.0.0.1:8080)
echo   2. 没有其他程序占用 GPU
echo.
set /p LIMIT=限制题数(直接回车 = 全部): 
echo.
if "%LIMIT%"=="" (
  python runner\run.py --endpoint local --suite all --repeats 3
) else (
  python runner\run.py --endpoint local --suite all --repeats 3 --limit %LIMIT%
)
if errorlevel 1 goto fail
echo.
echo 生成榜单...
python runner\build_report.py
python runner\make_leaderboard.py
goto end
:fail
echo.
echo 评测失败,见上面的报错。
:end
pause