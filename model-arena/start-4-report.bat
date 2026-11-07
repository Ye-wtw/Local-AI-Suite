@echo off
chcp 936 > nul
cd /d "%~dp0"
echo ==================================================
echo   生成榜单
echo ==================================================
echo.
python runner\build_report.py
if errorlevel 1 goto fail
python runner\make_leaderboard.py
if errorlevel 1 goto fail
echo.
echo 完成,打开 leaderboard.html
start "" leaderboard.html
goto end
:fail
echo.
echo 生成失败,见上面的报错。
:end
pause