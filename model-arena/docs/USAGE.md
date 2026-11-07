# 使用说明

> 照着这条路径走，从零到出结果约 30 分钟。
> 架构说明见 `ARCHITECTURE.md`，改代码前读 `AGENTS.md`。

---

## 0. 前置检查

```bash
cd model-arena
python runner/setup.py
```

会检查 Python 版本、GPU、llama-server、模型文件、题库、公开基准。
**缺什么会直接告诉你**，不用猜。

---

## 1. 第一次跑：不用 GPU，不用 API key

```bash
# 终端 A：起假模型
python runner/mock_server.py --port 8099

# 终端 B：跑 24 题
python runner/run.py --endpoint mock --suite smoke --skip-vram-check
```

预期输出（实测 0.4 秒）：

```
[SMOKE] 24 题  通过 16  准确率 66.7%  耗时 0.4s
    D10  3/3  100%
    D2   4/4  100%
    D3   2/4   50%
    D4   1/3   33%
    D5   2/4   50%
    D6   1/3   33%
    D8   3/3  100%
```

**这一步的意义**：验证管线通不通。如果这里报错，后面跑真模型也会报错。

---

## 2. 接本地模型

### 2.1 启动引擎

llama.cpp 最小可用命令（本机实测 8GB 显存可跑 35B-A3B）：

```bash
llama-server -m <model.gguf> \\
  --ctx-size 32768 --n-gpu-layers 99 --parallel 1 --n-cpu-moe 30 \\
  -ctk q4_0 -ctv q4_0 -fa on -b 512 -ub 512 \\
  --reasoning off --host 127.0.0.1 --port 8080
```

**三个必须有**：

| 参数 | 不加会怎样 |
|---|---|
| `--parallel 1` | 默认 4，上下文被切成 1/4 |
| `--reasoning off` | `content` 为空，所有字进 `reasoning_content` |
| `-ctk q4_0 -ctv q4_0` | 显存不够装不下 32k 上下文 |

### 2.2 确认端点

```bash
python runner/engines.py --detect --port 8080
```

会告诉你：引擎类型（llama.cpp / Ollama / 通用）、支持哪些探测端点。

### 2.3 跑起来

```bash
# 快速验证（24 题，约 5 分钟）
python runner/run.py --endpoint local --suite smoke

# 主力档（137 题，约 25 分钟）
python runner/run.py --endpoint local --suite canary --tag my-first
```

**中断了怎么办**：直接重跑同一条命令，会从断点继续。
每题实时落盘，强杀最多丢一题。

---

## 3. 接云端模型

```bash
# key 只放环境变量，绝不写进配置文件
export DEEPSEEK_API_KEY=sk-xxxx        # Linux/macOS
$env:DEEPSEEK_API_KEY='sk-xxxx'        # PowerShell

python runner/run.py --endpoint deepseek-flash --suite canary \\
  --skip-vram-check --tag cloud-1
```

端点定义在 `config/endpoints.json`，用 `api_key_env` 字段指定环境变量名：

```json
{
  "deepseek-flash": {
    "base_url": "https://api.deepseek.com",
    "model": "deepseek-flash",
    "api_key_env": "DEEPSEEK_API_KEY"
  }
}
```

**成本参考**：deepseek-flash 跑全量 1949 题约 14 元。
先用 canary（137 题）约 1 元。

---

## 4. 出结果

### 4.1 榜单

```bash
python runner/build_report.py
```

输出到 `results/leaderboard.json` 和 `reports/leaderboard.md`。

**看到这段要警觉**：

```
!! 判分器版本核对（当前 6696c10c96fa）
   241 个维度没有版本标记（早于版本机制引入）
```

意思是：这些分数是旧判分器算的，**和新分数不可比**。
处理办法见 4.3。

### 4.2 配对比较（最有信息量）

```bash
python runner/arena_cli.py compare --a local --b deepseek-flash --level canary
```

会给出：谁在哪题赢、McNemar p 值、逐维度差异。
**这比看总分有用**，因为总分被权重影响。

### 4.3 判分器改了之后

```bash
# 1. 看当前版本
python runner/judge_version.py

# 2. 用新判分器重算历史数据（不重跑模型，零成本）
python runner/rejudge.py --latest --level full --write

# 3. 重新出榜单
python runner/build_report.py
```

重判会打印哪些题判定变了，例如：

```
d8_fab_0003   False -> True   | {"correct": true, "kind": "unverifiable", "hedged": true}
d4_sql_0001   True -> False   | {"correct": false, "stage": "mismatch", ...}
```

---

## 4.2 跑云端之前：先看要花多少钱

```bash
python runner/arena_cli.py cost
```

```
维度      题数    输入token    输出token     成本元
D7         120   14649264       18000     14.69   <- 占九成
其余      3934                            1.51
合计      4054                           16.20

不含 D7: 3934 题, 1.51 元
仅 D7  : 14.69 元（长上下文是成本主因）
```

**D7 只有 120 题却占九成成本**，因为其中 60 题需要 128k-256k 上下文。

### 跑付费端点会强制确认

```
---- 付费端点花费预估 ----
端点 https://api.deepseek.com
套件 canary     约 137 题，预估 0.55 元
提示：D7 长上下文 120 题占九成成本，日常评测可避开 --suite longctx

继续请输入 yes，其它任意键退出：
```

- 输入非 `yes` -> 退出，不产生任何结果文件
- 非交互环境（脚本/CI）-> 需要 `--yes`，否则退出码 5
- **本地环回地址不提示**（`127.0.0.1` / `localhost`），因为免费
- 付费网关跑在环回上时，在端点配置里写 `"cost": "paid"` 强制开启

## 4.3 选模型：attach 与 launch 两种模式

这是本项目最容易混淆的一点，也是它和普通评测工具的区别。

### attach —— 服务已由你自己启动，本程序只连

```bash
# 你手动起 llama-server，本程序只指向它
python runner/targets.py scan                      # 扫描本机在跑的服务
python runner/targets.py probe --url http://127.0.0.1:8080
python runner/targets.py add-attach --url http://127.0.0.1:8080 --id mymodel
```

**代价**：本程序看不到启动参数，所以结果里 `params_known: false`。
同样的模型换个 KV 精度分数就会变，而 attach 模式下这个信息丢失了。

### launch —— 本程序启动引擎，参数完全已知

```bash
# 只生成命令，不启动（安全的默认）
python runner/launcher.py --model "E:\...\xxx.gguf" --ctx 32768 --cpu-moe 30

# 通过 API 启动
curl -X POST http://127.0.0.1:8098/api/launch -d '{"model":"...gguf","ctx":32768}'
```

**收益**：命令行是本程序拼的，所以 `params_known: true`，每个分数都能追溯到
`--ctx-size / --n-gpu-layers / -ctk -ctv`。

**两条强制项**（写在 `launcher.py` 里，不可关闭）：

| 参数 | 为什么必须有 |
|---|---|
| `--parallel 1` | 默认 4 会把上下文切成 1/4，32k 变 8k，长上下文分数无故下降 |
| `--reasoning off` | 开着时 `content` 为空，全在 `reasoning_content`，判分器读到的就是空回答 |

### 控制面接口（给 UI 用）

```http
GET  /api/targets                    列出所有目标
GET  /api/target/<id>                读一个
POST /api/targets                    新增/覆盖 {"target":{...}}
POST /api/discover                   扫描端口 + 列本地 gguf
POST /api/launch                     启动引擎 {"model":"...gguf","ctx":32768}
POST /api/attach                     探测并保存一个 url
```

## 4.4 跑长任务时：终端仪表盘

全量评测要 5-10 小时，CLI 只有滚动的进度行。开着这个窗口能看到全局：

```bash
python runner/arena_tui.py            # 每 2 秒刷新，q 退出，r 立即刷新
python runner/arena_tui.py --once     # 只渲染一帧
python runner/arena_tui.py --plain    # 无 ANSI，适合重定向到文件
```

显示三块：

```
 Model Arena  2026-11-01 21:05:10
================================================================
  endpoint : UP   VRAM 6682/8151 MiB   judge 6696c10c96fa

  进行中
    canary   local   [#####-----------]  62 题  运行中  1.4 题/秒

  最近结果
    LEVEL_canary_local_...json  canary  local  137  91.2%  11-01 20:55
```

**只显示实测的速率，不猜剩余时间**——检查点里没存总题数，算出来的 ETA 会是编的。

也可以 `python runner/arena_cli.py tui`。

## 5. 四个常用子命令

```bash
python runner/arena_cli.py status     # 已评测的模型概览
python runner/arena_cli.py env        # 当前机器干扰状况（跑前必看）
python runner/arena_cli.py compare --a X --b Y
python runner/arena_cli.py run --suite canary --endpoint local --yes
```

`run` 不加 `--yes` 只打印命令不执行——防止误触占用 GPU 几小时。

---

## 6. 跑之前的检查清单

```bash
python runner/arena_cli.py env       # ① 有没有别的进程占显存
python runner/selfcheck.py           # ② 题库结构有没有问题
python runner/doccheck.py            # ③ 改过文档就跑一下
python runner/judge_version.py       # ④ 记下当前版本
```

**自检应该输出**：

```
findings: 0  HIGH=0  MED=0
```

不为 0 就别跑——问题会污染整轮数据。

---

## 7. 读数时最容易犯的三个错

### ① 加权总分不等于能力

权重在 `config/weights.json`，是自设计的。换一组权重排名可能翻转。
**看逐维度**，或直接用 `compare` 做配对。

### ② 小样本差异是噪声

| n | Δ10pp 检出率 | Δ20pp 检出率 |
|---:|---:|---:|
| 25 | 2% | 34% |
| 105 | 92% | 100% |
| 200 | 88% | 100% |

137 题的 canary 能检出 8pp 以上差异。**2pp 差距不要当结论。**

### ③ 覆盖度不足的模型不该排第一

只跑了「数学」一个维度也可能 100%。
覆盖度低于 60% 的结果不进排名。

---

## 8. 常见问题

**Q: 跑到一半崩了，数据丢了吗？**
不丢。每题实时落盘，重跑同命令从断点继续。

**Q: 分数怎么比昨天低了？**
先看 `judge_version`。版本不同，分数不可比。

**Q: 某维度全是 0 分？**
八成是判分器或题面问题，不是模型问题。
跑 `selfcheck.py`，再抽几题看模型实际回答。

**Q: 云端跑 full 要多少钱？**
deepseek-flash 约 14 元 / 1949 题。建议先 canary（约 1 元）。

**Q: 显存不够怎么办？**
降 `--ctx-size`，或降 `--n-gpu-layers`（部分层放 CPU）。
注意 unset `--parallel` 以外的参数不要乱改，会破坏预设。

**Q: 长上下文题被跳过了？**
引擎 ctx 小于题目要求。看日志里的 `[ctx gate] 跳过 N 题`，
要跑全部需 `--ctx-size` ≥ 题目要求（D7 最高 256k）。

---

## 9. 完整命令参考

```bash
# 评测
python runner/run.py --endpoint <ep> --suite <level> [--limit N] [--tag 名字] [--no-resume]

# suite 可选（all 不含 vision / hint，见下）
math speed mcq instruction chess longctx code agent reliability
realworld dynamic compression public lcb smoke canary full probe calib coarse all

# 以下两个必须单独跑，不进 all：
python runner/run.py --endpoint local --suite vision   # 引擎需带 --mmproj
python runner/run.py --endpoint local --suite hint     # D8 幻觉对照实验组

# 公开基准（2037 题，9 个权威集，配比均衡）
python runner/run.py --endpoint local --suite public
python runner/run.py --endpoint local --suite lcb      # 只看 LiveCodeBench v6

# 报告
python runner/build_report.py
python runner/make_leaderboard.py     # → leaderboard.html

# 维护
python runner/selfcheck.py [--strict]     # 题库结构自检
python runner/doccheck.py [--strict]      # 文档里的命令是否真实存在
python runner/rejudge.py --latest --level <level> [--write]
python runner/make_subset.py --all
python runner/judge_version.py

# 端点探测
python runner/engines.py --detect --port 8080

# 参数扫描
python runner/sweep.py --stage 0       # 生成方案（零成本）
python runner/sweep_stages.py --stages # 看漏斗
python runner/sweep_report.py          # 出结论
```

---

## 10. 下一步

- 想看**参数对能力的影响** → `sweep.py` 系列
- 想看**两个模型的差异** → `arena_cli.py compare`
- 想**加新维度** → 读 `ARCHITECTURE.md` 的「加一个新维度要动哪里」
- 想**改判分器** → 必读 `AGENTS.md` 第 4 条的反例清单
