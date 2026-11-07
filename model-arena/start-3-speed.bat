@echo off
chcp 936 > nul
cd /d "%~dp0"
echo ==================================================
echo   只跑速度扫描 (不跑题本,约 30 秒/组)
echo ==================================================
echo.
set /p REPEATS=重复次数(直接回车 = 3): 
if "%REPEATS%"=="" set REPEATS=3
echo.
python runner\run.py --endpoint local --suite speed --repeats %REPEATS%
if errorlevel 1 goto fail
echo.
echo 生成榜单...
python runner\build_report.py
python runner\make_leaderboard.py
goto end
:fail
echo.
echo 速度扫描失败,见上面的报错。
:end
pause