@echo off
chcp 936 > nul
cd /d "%~dp0"
echo ==================================================
echo   model-arena 自检 (不需要真模型)
echo   起一个 mock 端点,跑完整判分链路 + 速度扫描
echo ==================================================
echo.
echo [1/3] 启动 mock 端点 (端口 8099)...
start "mock-server" /min python runner\mock_server.py --port 8099 --acc 0.8
rem 固定等 4 秒不够稳:机器忙时 mock 起得慢,D2 会在第一题就报
rem "chat failed after 3 attempts" 而整轮自检判失败(实测在干净解包里复现过)。
rem 改成轮询等就绪,最多 40 秒;连不通就说清楚是 mock 没起来,而不是甩一堆 traceback。
set /a _try=0
:waitmock
timeout /t 2 > nul
set /a _try+=1
python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8099/v1/models',timeout=3).status==200 else 1)" > nul 2>&1
if not errorlevel 1 goto mockok
if %_try% lss 20 goto waitmock
echo.
echo   mock 端点 40 秒内没起来。检查 python 是否可用、8099 是否被占用。
goto fail
:mockok
echo   mock 就绪。
echo.
echo [2/3] 跑各维度 + 速度...
rem 这里原先写的是 --suite all。all 会连 public(2037 题) 和 lcb 一起跑,
rem 而 datasets/public/ 在 .gitignore 里(体积大、要 fetch_bench.py 下载) ——
rem 于是刚解包的人自检必然崩,而且崩在很后面才发生,看起来像"项目坏了"。
rem 自检要验的是"判分链路通不通",不是跑公开基准,所以按维度列出、不含 public/lcb。
rem 公开基准要跑,是用 start-2-evaluate.bat 或 run_all.py,不是自检。
rem --suite 一次只收一个值(单值参数,不是多值),所以要逐个跑。
rem 之前写 --suite all 会连 public(2037 题)+lcb 一起跑,而那两份数据在
rem .gitignore 里(要 fetch_bench.py 下载),刚解包的人必然崩。
rem 自检验的是"判分链路通不通",按维度逐个跑即可,不碰公开基准。
for %%S in (code,reliability,instruction,agent,chess,math,realworld,mcq,longctx,compression) do (
  python runner\run.py --endpoint mock --suite %%S --repeats 3 --skip-vram-check
  if errorlevel 1 goto fail
)
if errorlevel 1 goto fail
echo.
echo [3/3] 生成榜单...
python runner\build_report.py
python runner\make_leaderboard.py
echo.
echo 自检完成。打开 leaderboard.html 查看。
goto end
:fail
echo.
echo 自检失败,见上面的报错。
:end
taskkill /f /im python.exe /fi "WINDOWTITLE eq mock-server*" > nul 2>&1
pause