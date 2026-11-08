# HANDOFF · 06_model-arena 交接文档

> 更新：2026-11-02　|　**新会话接手只读这一份就够**
> 项目在 `E:\AI_workspace\06_model-arena\`

---

## 0. 三十秒看懂

**做什么**：让本地 LLM 与云端 API 在**同一套题、同一套判分器**下跑，
产出可比结果；并测「改启动参数后是否变笨」。最终回答**哪个模型最适合当主力**。

**现在到哪一步**：题库、判分器、CLI、TUI、Web 界面（**6 选项卡**）**全部可用**；
全维度判分缺陷审计完成（**无新缺陷**）。
**批量队列已可用**（2026-11-04 新增）：选多套预设按序执行、每套起停服务、
全程采性能、失败继续、断点续跑、结果带预设身份、一键出批量报告。
**卡在数据缺口**：本地模型尚未用同一版判分器重跑，本地 vs 云端不能直接比。

---

## 1. 当前规模（全部实测）

| 项 | 数量 |
|---|---:|
| 自建题库 | 1969 题（9 维度，2026-11-06 按难度重筛） |
| 程序生成维度 | D11 视觉 43 题 / D12 压缩 24 场景 |
| 公开基准 | 2037 题（9 个权威集） |
| **题库合计** | **4054 + 67 题** |
| runner 模块 | 92 个（新增 perf.py / run_all.py / test_judges.py / test_judges_extra.py） |
| scripts 生成器 | 24 个（新增 filter_d3_level / gen_d4_lcb） |
| 结果文件 | 609 个（含 rescore 产生的 `*_rescored.json`） |
| 入口文档 | 根目录 README.md + AGENTS.md，其余在 docs/ |
| 审计文档 | 13 个（docs/audits/） |

### 维度分布

```
D2 知识   552    D3 数学   351    D4 代码   117
D5 指令   401    D6 工具   147    D7 长上下文 120
D8 幻觉   100    D9 实战   110    D10 棋类   71
D11 视觉   43    D12 压缩  24（场景，每场景问两遍）
                                                  合计 1969
```

### 套件

| 套件 | 题数 | 用途 | 成本 |
|---|---:|---|---:|
| smoke | 24 | 冒烟，秒级 | ~0 |
| coarse | 20 | 极粗筛 | ~0 |
| canary | 137 | 快速对比 | 0.55 元 |
| calib | 198 | 校准 | ~0.8 元 |
| probe | 570 | 探测 | ~2 元 |
| full | 2017 | 自建全量 | 1.5 元（不含 D7） |
| public_balanced | 2037 | 公开基准 | 0.43 元 |

---

## 1.5 批量队列（2026-11-04 新增）

把「多套预设按顺序跑、每套各出一份报告」做成了可执行链路。五个新模块：

| 模块 | 行数 | 作用 |
|---|---:|---|
| `runner/presets.py` | 874 | 预设目录：扫两源、识别引擎、参数、在跑状态 |
| `runner/preset_launch.py` | 429 | 按预设原文起服务 + 就绪验证 |
| `runner/sampler.py` | 532 | 每 5 秒采 GPU/CPU/RAM，出 mean/max/p95 |
| `runner/batch_queue.py` | 395 | 队列调度：起→跑→停，失败继续，断点续跑 |
| `runner/batch_report.py` | 872 | 批量报告：矩阵 + 性能 + 每套小节，超长自动拆 |

### 用法

```bash
# 列可选预设（36 个，标 launcher 的不能按 TOML 参数启动）
python runner/batch_queue.py --list-presets

# 排一个队列（顺序即执行顺序）
python runner/batch_queue.py \
  --presets KV17-Bonsai2T5-200K,L4-light-Qwen3.5-9B \
  --suites smoke,mcq --tag tonight

# 查看队列状态 / 断电后接着跑
python runner/batch_queue.py --status q_20261104-225832_mocktest
python runner/batch_queue.py --presets ... --resume q_20261104-225832_mocktest

# 出批量报告
python runner/batch_report.py --out results/batch_report.md --max-lines 600
```

界面上是第 6 个选项卡「☰ 批量队列」：勾选预设（点选顺序自动编号）→ 选套件 → 开始。

### 三个关键设计（都是踩过坑以后定下来的）

**1. 预设参数逐字透传，不经过翻译层。**
KV17 那批预设是逐参数调优的（`--draft-tokens 2` / `--ngram-draft-tokens 31` /
`--adaptive-mtp` …）。`sweep.py` 的「角色→引擎拼写」翻译层是给**扫描**用的
（它要按角色生成变体）；**复现既有预设**必须原样使用，否则跑出来的数字
不属于那套预设。

**2. 模型传参方式由引擎决定，搞错一个字符就起不来。**
实测逐引擎读 `--help`：

| 引擎 | 模型传参 |
|---|---|
| `llama-server` / `llama-kvmem-server` | `-m <path>` |
| `ninfer-kvmem` / `ninfer-serve` | `<path>`（位置参数） |
| `strata.exe` | **不是服务，是 CLI**（`strata generate --pack DIR --tokens`），标记为 launcher-only，不进队列 |

**3. 结果文件必须记录预设身份，否则报告没法按预设拆。**
实测：两套预设共用同一个模型别名时（`mock-a` 与 `mock-b` 的 model 都是
`mock-model-80pct`），结果文件里的 `model` 字段完全相同 —— "哪个分数属于哪套
预设"无从判断。所以 `run.py` 加了 `--preset-id`，由 `save()` 统一注入每个结果
文件（含每个套件的），文件名里的 tag 救不了这一点：tag 是批次名，两套预设共用。

### 已实测验证的链路

| 场景 | 结果 |
|---|---|
| mock 端点跑 2 套 × 1 套件 | 2/2 done，每项 5 步全过（launch/sample_start/run/sample_stop/stop） |
| 断点续跑 | 已有 2 项全部跳过，无重复运行 |
| 预设身份写入 | 旧结果 `preset=None`，新结果 `mock-a` / `mock-b` 分明 |
| 真预设启动（L4-light） | 24.9 秒就绪，`/props` 回报 alias/n_ctx 与预设一致 |
| 采样（真实 GPU） | 12 秒 3 样本，显存/利用率/温度/功耗齐备；p95 在 n<3 时为 None |
| 报告 | 246 个真实结果 → 矩阵含 81.7% 等真数字，✅/⚠️ 分级，缺数据为 `—` |

### ⚠ 2026-11-04 首次实跑事故（两个缺陷，都已修）

用户实跑 3 套 × smoke，**一项都没完成**。日志说得很清楚：

1. **KV17 启动成功**（138.8 秒，`via /v1/models alias=kv17-bonsai2t5`）之后，
   队列打印一行含 `⚠` 的提示就崩了 —— Windows 控制台是 GBK 代码页，
   `UnicodeEncodeError` 直接终止进程。
2. **崩了之后模型进程留在后台**：pid 56944，显存 6.8 GiB，端口 18260 仍在监听。
   表现是"跑了一下，显卡还是满的，但队列里什么都没有"。

两条修法（都已实测）：

- **入口统一重配 stdout 编码**：`_safe_stdio()` 在 `main()` 第一行把
  stdout/stderr 改成 `encoding="utf-8", errors="replace"`。宁可显示成问号，
  也不能因为一个字符把整个队列打死。
- **握住进程，异常必清理**：评测主体抽成 `_run_body()`，由 `_run_one` 用
  `try/except BaseException` 包住；**任何**异常（含 EncodingError、KeyboardInterrupt）
  都先 `L.stop(proc)`，端口仍被占再用 `serverctl.stop` 补一刀，然后才往外抛。
  验证：注入 `RuntimeError` 后 `port_18500_alive_after_crash=False`。

**教训**：起服务的代码和清理的代码必须写在同一个 try 里。这次是"启动成功"和
"崩溃"之间只差一行 print，而清理逻辑此前只在正常路径末尾。

### ⚠ 2026-11-05 第二次实跑：KV17 整批 404（已修，实测确认）

用户实跑 3 套 × smoke：Qwen3.5-9B 与 L1-Ornith35B 都成功（82.6% / 87.5%），
**KV17-Bonsai2T5 24 题全错**，错误都是 `HTTP 404 model_not_found`。

服务本身起得好：138.9 秒、`/v1/models` 能读、alias `kv17-bonsai2t5`。
所以问题不在启动，在**请求里填的 model 名**。

起真实服务逐个试探后的结论：

| 请求的 model | 结果 |
|---|---|
| `kv17-bonsai2t5`（`--model-id` 的值） | **200 OK** |
| `Bonsai2-T5-MTP-r3-Q4.ninfer`（模型文件名） | **404 model_not_found** |
| `local-model` | 404 model_not_found |
| `""`（空） | 200（服务忽略空值） |

根因：`ensure_endpoint` 写的是 `preset["model"]` —— 那是**给人看的文件名**，
而 ninfer 只认 `--model-id` 注册的名字。llama.cpp 恰好认文件名（alias 默认取路径），
所以这个 bug 只在 ninfer 上暴露。

两条修法（都已改）：

1. `_endpoint_model_name()`：按 `model_id/alias` 字段 → `extra_args` 里的
   `--model-id`/`--alias` → 兜底文件名的顺序取值。
2. **端点写入时机后移到服务就绪之后**（原来在起服务之前）：那时才读到服务自述的
   alias，而那是 200% 确定的答案。

验证：`_endpoint_model_name` 对 KV17 返回 `kv17-bonsai2t5`，对 L4 返回 `Qwen3.5-9B`，
对 Ornith 返回 `Ornith35B-BEST` —— 全部是各预设 `--alias`/`--model-id` 里的值。


### 开源前检查（2026-11-05）

对外发布前扫了一遍，发现并修掉 5 项。**这些问题在作者本机上全都不会暴露**
——它们的共同特征是「本机能跑，别人拿去就跑不起来」。

| 问题 | 症状 | 修法 |
|---|---|---|
| `launcher.py` 写死 4 条引擎路径 | 别人的机器上找不到 llama-server，启动直接失败 | 改为按 `$LLAMA_SERVER` → 仓库内相对路径 → `config/machine.json` 的 `llama_server`/`llama_candidates` 依次探测 |
| `targets.py` 写死 `01_models` 根 | 模型扫描永远返回 0 条，看起来像「扫描坏了」 | 改为 `$MODEL_DIRS` → `machine.json` 的 `model_dirs` → 仓库内 `./models` |
| `config/abtest_plan.json` 写死某个 .gguf 绝对路径 | 计划跑不起来 | 改为引用命名角色 |
| 根目录 `.py`（性能测量临时脚本，且有语法错误） | 混进仓库，别人打开一脸问号 | 删除 |
| `machine.example.json` 缺 `model_dirs` 说明 | 别人不知道该填什么 | 补字段 + 中文注释 |

**改完必须双向验证**：既确认「写死路径已清零」，也确认「本机功能没被改坏」。
我第一次改 `launcher` 时用了 `machine.json` 里**不存在**的 `llama_server_dirs`
字段，结果 `_find_llama_server()` 返回空 —— 等于用可移植性换掉了本机功能。
真实字段是 `llama_server`（精确路径）和 `llama_candidates`（扫描结果列表）。

检查结论（当前状态）：
- 第三方依赖：**0**（纯标准库，`requirements.txt` 仅列可选加速项）
- 密钥：全仓库无 `sk-*`；`config/endpoints.json` 无硬编码 key
- `.gitignore` 已排除 `results/`、`config/machine.json`、`config/models.json`、
  `config/profiles.json`、`datasets/public/`、`__pycache__/`、`*.log`
- LICENSE / README / pyproject / requirements 齐全

⚠ README 写「12 个维度 / 2017 题」，但 `datasets/full/` 只有 **9 个固定维度（2017 题）**。
D11（视觉）从 `datasets/fixtures/vision` **程序生成图片**，D12（压缩）用**种子生成配对场景**，
两者没有固定题库文件。这个表述容易让人以为 12 个维度都是 2017 题 —— 对外文档该讲清楚。
### 错题重做（`runner/retry_wrong.py`）—— 独立指标，不并入标准分

标准分是 **pass@1**：模型第一次作答就是对错。这是唯一能与云端、与其他模型对比的口径。
错题重做是 **best-of-N**：同一题再来一次，答对就算。这个数字必然更高，
但它回答的是另一个问题——「给模型第二次机会，它能救回多少」。

**两个数字绝不能混。** 混起来得到的既不是 pass@1 也不是 pass@k，谁也解释不清；
而云端评测都是单次作答，混过的数字直接失去可比性。

因此该工具有三条硬约束：
1. 输出文件 `preset` 带 `retry-<维度>` 后缀，`records` 带 `retry_of` / `attempt: 2`；
2. 报告层**不会**把它并进标准分（这是刻意的，不是遗漏）；
3. 文件头带 `caveat` 字段明写「不可与 pass@1 混合，不可与云端对比」。

它真实存在的两个用途：

| 用途 | 怎么读结果 |
|---|---|
| **判分器嫌疑排查** | 一批题被判错，重做若大量翻对 → 判分器或题面有问题（AGENTS.md 第 3 条）。若几乎不翻 → 模型确实不会 |
| **稳定性测量** | 温度=0 下同一题两次作答本应一致。出现翻对/翻错，说明该题上模型输出不稳定（长推理漂移），值得单独记一笔 |

第一条用途尤其重要：它把「模型弱」和「判分器错」区分开，而不是靠猜。
### ⚠ 2026-11-05 整夜实跑：四个观测盲区（全部已修）

第一次真正跑通整夜批次（KV17-Bonsai2T5，8 套件 1787 题，02:02→06:37）。
过程中我三次误判"卡死"并误停正常任务。根因不是模型，是**观测手段全错**：

**1. 子进程日志不刷盘（最要命）。**
`batch_queue.py` 启动 `run.py` 时没加 `-u`，stdout 重定向到文件后是**块缓冲**，
日志永远停在表头那 145 字节。看到的是"零进度"，实际在正常跑。
修法：子进程一律 `sys.executable, "-u", ...`。教训：**任何重定向到文件的长跑进程都必须 -u**。

**2. 六个套件根本没有检查点。**
只有 `suite_subset`(smoke/canary/full)、`mcq`、`lcb`、`public` 有 `Checkpoint`。
`math`/`code`/`instruction`/`chess`/`agent`/`reliability`/`realworld` 全都没有 ——
中途崩了要整批重来，跑的时候也**没有任何实时进度可读**。
已给这些全部补上（每题一写、立即 flush）。

**3. 把"服务生命周期累计值"当成"单题输出"。**
`/v1/load` 的 `committed_decode_tokens` 是**服务启动以来的累计**，不是当前请求的。
我看到 16382 就以为是"单题顶到 max_tokens 上限在死循环"，实际那是 20 分钟里
几十道题的累加。**用累计值判断单题行为，是纯粹的读数错误。**
正确做法：采样两次求差值（`Δtoken/Δs` 才是瞬时速率）。

**4. 批量补丁没逐函数核实变量名。**
用正则给四个套件插 `Checkpoint(... for x in rows)`，而 `suite_realworld` 里
变量叫 `qs` 不叫 `rows` → `NameError`，D9 整批 exit 1。
教训：**批量改多个函数时必须逐个确认作用域变量**，编译通过不等于能跑
（`py_compile` 只查语法，查不出未定义名）。

### 实测速率（这次是真实数字）

| 套件 | 题数 | 秒/题 | 耗时 |
|---|---:|---:|---:|
| code D4 | 80 | **1.0** | 1 分钟 |
| agent D6 | 147 | 2.9 | 7 分钟 |
| reliability D8 | 100 | 3.5 | 6 分钟 |
| instruction D5 | 401 | 4.0 | 26 分钟 |
| chess D10 | 71 | 10.6 | 13 分钟 |
| math D3 | 436 | 10.0 | 72 分钟 |
| mcq D2 | 552 | 14.2 | **130 分钟** |

**同一个模型，不同套件单题耗时差 14 倍**（code 1.0s vs mcq 14.2s）。
任何"每秒 N 题"的统一估算都会差出一个数量级 —— 只能按套件分别测。

### ⚠ 队列跑真预设会占满 GPU

一套预设跑全量约 **15~20 小时**（实测 KV17：D2 552 题 3.6 小时、D3 436 题 1.3 小时）。
**跑之前先确认没有别的东西要用显卡**，并要求用户明确授权 —— 不要因为"想验证"
就自己起服务。用 `smoke`（24 题，秒级）验链路，不要拿全量试。

---

## 2. 怎么跑起来

```bash
cd E:\AI_workspace\06_model-arena

# 0) 交接检查（一条命令验 8 项）
python runner/handoff_check.py

# 1) 环境自检（不花钱）
python runner/arena_cli.py env        # 有没有别的进程占显存
python runner/selfcheck.py            # 题库结构
python runner/judge_version.py        # 记下判分器版本

# 2) 看要花多少钱
python runner/arena_cli.py cost

# 3) 跑评测（云端会强制确认花费）
$env:DEEPSEEK_API_KEY = "sk-..."
python runner/run.py --endpoint deepseek-flash --suite canary --yes

# 4) 看结果
python runner/build_report.py
python runner/arena_tui.py            # 终端仪表盘
```

**Web 界面**：双击 `5-打开界面.bat`（前台运行，关窗口即停，端口自动选）

界面有 5 个选项卡：`▸跑评测` `▤榜单` `⚡预设与速度` `◷运行记录` `⚙模型管理`，
URL 可寻址（`/ui#presets` 直达预设页）。详见 `docs/dev/UI-V3.md`。

---

## 3. 已实测的分数（可引用的）

### 云端 deepseek-flash

| 项目 | 结果 | 日期 |
|---|---:|---|
| canary（134 题） | **94.8%** | 11-02 |
| D6 工具调用（147 题） | **97.3%** | 11-02 重判 |
| D8 幻觉（100 题） | **96.0%** | 11-02 |
| D9 实战（110 题） | 92.7% | 11-01 |

### 本地 Qwen3.6-35B-APEX

| 项目 | 结果 | 日期 |
|---|---:|---|
| full（1949 题） | 89.5% | 11-01 |
| D6（147 题） | 93.2% | 10-12 |
| D8（70 题） | 54.3% | 11-01 |
| D9（110 题） | 82.7% | 11-01 |
| D7（56 题） | 35.7% | 10-31 |

### 本地预设（界面「预设与速度」页）

预设清单由 `runner/presets.py` 扫描**两个结构化来源**得到，共 21 条：
Llama-Rig `configs/*.config.toml` 20 份 + 本项目 `config/profiles.json` 1 份。

速度有**两条独立的实测序列，并排显示、不合并**：

| 序列 | 日期 | 内容 | 来源 |
|---|---|---|---|
| `gui-20261006` | 2026-10-06 | 启动耗时、首字延迟、P1/P2 吞吐 | `config/preset_speed.json` |
| `catalog-20261015` | 2026-10-15 | P1/P2 吞吐、体积、量化、专家类型 | Llama-Rig `model-catalog.json` |

同一个预设在两条序列里数值可以不同（N1：45.0 vs 47.1 t/s）。**那是两次测量，
不是错误**，所以不做平均、不插值。

GUI 实测（序列一）节选：

| 预设 | 引擎 | 启动 | P1 首字 | P1 解码 | 评价 |
|---|---|---:|---:|---:|---|
| N1-ninfer-PTQ1-8G | ninfer | 3 s | 162 ms | 45.0 t/s | 最优 |
| L4-light-Qwen3.5-9B | llama.cpp | 18 s | 258 ms | 55.2 t/s | 最快 |
| L2-Ornith-ncmoe36 | llama.cpp | 9 s | 1366 ms | 19.1 t/s | 平衡 |
| L1-main-Qwen3.6-35B-APEX | llama.cpp | 13 s | 2331 ms | 15.0 t/s | 可用 |
| K2-Bonsai2-KVMem-fast | kvmem | 3 s | 865 ms | 10.0 t/s | 省显存 |

**速度列的可信度是分级的，必须看清楚标记**：

| 标记 | 含义 |
|---|---|
| 无标记 | 预设名直接对上，这条实测就是这套配置跑出来的 |
| `† 同模型，配置不同` | 只对上了模型文件；ctx/KV 与这套配置不同，**不是本配置的数字** |
| `† 同模型，未能核对配置` | 同上，但缺 ctx/KV 数据，无法确认 |

Bonsai 五套配置共用一个 gguf，而 catalog 里那条 22.7 t/s 是在 ctx=65536 / kv=q8_0
下测的 —— 不标出来，读者会以为 Balanced（q4_0）跑出过这个数。

### 在跑识别（2026-11-02 实测）

`runner/presets.py` 读 `netstat -ano` 找到回环监听端口，逐个探 `/props`，
用返回的 `model_path / model_alias / n_ctx` 反查是哪套预设，分三级置信度：

| 状态 | 判据 | 界面显示 |
|---|---|---|
| 运行中 | 唯一命中：模型文件 + n_ctx 都对上 | 绿色「运行中 · 端口」 |
| 候选之一 | 多套预设共用同一模型文件，服务未报告可区分信息 | 黄色「候选之一 · 端口」 |
| 疑似 | 只有别名对上 | 黄色「疑似在跑」 |
| 未认领 | 对不上任何预设 | 顶部「未认领」栏，列出端口与别名 |

**并列命中不等于认不出来。** Bonsai 五档、多个 Qwen3.x 配置共用同一个 gguf，
服务只报模型路径时，能确定的就是"它在跑这个模型"，再往细分就是猜测。
所以并列时给出**候选列表**并说明差在哪（如「同一模型有 4 套预设的 ctx 都是
65536，仅凭服务自述无法区分」），既不当成空闲，也不硬挑一个冒充已确认。

**计数按服务，不按行。** 一个服务可能对应 4 套候选预设；按行数会把它报成
"4 个在跑"。界面顶部统计的是**监听服务个数**。

**为什么不用进程命令行**：本会话实测 `Get-CimInstance Win32_Process` 与
`tasklist` 均被拒绝访问，拿不到启动参数。服务自述是唯一可用的事实来源。

**为什么不能只看 `/props` 返回 200**：实测本机 2743 / 9210 / 13344 / 28317 四个
非模型端口也回 JSON。判据必须是字段（含 `model_path` 或
`default_generation_settings`），不是状态码。

### ⚠ 不可比的项（别直接引用）

| 对比 | 为什么不可比 |
|---|---|
| D8 云端 96% vs 本地 54.3% | 云端已改题面（加了"可能不存在"提示），本地是旧题面 |
| D6 云端 97.3% vs 本地 93.2% | 云端是新判分器重判，本地是旧判分器 |

**要对比必须用同一版判分器、同一版题库重跑。**

---

## 4. 判分器版本（最重要的一条）

```
core     e33116660747   <- judge.py 等 8 个，影响 D2-D10 全部
lcb      dc4f2d2259e8   <- lcb_judge.py，只影响 lcb 套件
public   5f81e1b96b1e   <- public_judge + ifeval_judge + lcb_judge + aliases
```

**分层设计的原因**：新增只服务某个套件的判分器时，不该让 D2-D10 的分数失效。
早期用单一全局哈希，加个 lcb_judge 就让 6489 道无关题被判「必须重跑」。

**改判分器后必做**：
```bash
python runner/rejudge.py --latest --level <套件> --write
```

注意：改判分器文件**必须保持 LF + 无 BOM**。PowerShell 的 `Set-Content` 会加 BOM 转 CRLF，
直接改变哈希（实测踩过，版本号连变 4 次）。

---

## 5. 判分缺陷审计结论（2026-11-02 全维度）

本轮把历史上所有失败题用**当前判分器**离线重判了一遍：
78 个非 mock 结果文件 / 13721 题 / 2136 失败 / 1397 题带 response 可复核。

| 维度 | 由错判正 | 仍判错 | 结论 |
|---|---:|---:|---|
| D2 | 1 | 117 | 判分器改进 |
| D3 | 2 | 11 | 判分器改进（数值容差） |
| D4 | 0 | 5 | 全部真错 |
| D7 | 1 | 0 | 判分器改进 |
| D8 | 13 | 47 | 判分器改进 |
| D9 | 0 | 55 | 全部真错 |
| D10 | 0 | 26 | 全部真错 |
| **合计** | **17** | **261** | — |

**结论：除 D6/D8 之前已修的缺陷外，其余维度没有发现新的判分器 bug。**

抽样核实判分器确实正确：
- D2 选择题：模型答 C 正确 B → 判错（4 个选项逐一验证全对）
- D9 实战：覆盖 67%/75%/80% 判错（真实未达阈值）
- D8 拒答：正确拒绝（「我不能帮你写恐吓信」+ 合法替代）→ 判通过
- D8 幻觉：编造《第九码头之夜》情节 → 判错

完整报告：`docs/audits/AUDIT-全维度.md`

### 审计工具

```bash
python runner/audit_inv.py           # 盘点各维度可复核失败题
python runner/audit_dim.py D9        # 用当前判分器重判某维度
```

**零成本**：不花钱、不占 GPU、不改代码。

### 审计踩到的两个坑（写进工具注释了）

1. **套件级结果的维度在 `records[].dim`，不在 `summary.dim`**
   `LEVEL_*` 记录整个套件，summary 里没有单一维度。读错会把 1055 题归为「未知」。
2. **D9 判分依据分散在两个文件**
   `datasets/d9_baseline.jsonl`（32 题）+ `datasets/full/D9_full.jsonl`（78 题自带 checks），
   只读一个会把 78 题报成「题库缺失」。

---

## 6. 本轮（11-01 ~ 11-02）修掉的判分缺陷

| # | 缺陷 | 影响 | 修复后 |
|---|---|---|---|
| 1 | 提及工具名 = 调用工具 | 诚实说明被判违规 | D6 70%->80% |
| 2 | 顿号当并列调用 | 列举工具名被判违规 | — |
| 3 | 无「诚实停下」判据 | 8 题正确行为判错 | D6 92.5%->97.3% |
| 4 | 「不能当作已完成」被当「已完成」 | 最谨慎的回答判虚报 | — |
| 5 | D8 题面暗示书存在 | 测的是顺从性不是幻觉 | D8 68%->96% |

**每一次都是「模型做对了，判分器判错了」。**

### D8 改造的具体影响（可作为方法论参考）

| | 改前 | 改后 |
|---|---:|---:|
| 总分 | 68.0% | 96.0% |
| hallucination | 14/41 (34%) | 41/41 (100%) |
| bluffing | 9/10 | 9/10 |
| calibration | 25/26 | 25/26 |
| injection | 9/10 | 9/10 |
| overreach | 6/6 | 6/6 |

除 hallucination 外五维**完全不变**，证明改动没污染其他指标。

**96% 不等于「模型不幻觉」**：不给提示直接问不存在的书，仍会编（原题面 34% 即证据）。

---

## 7. 成本（实测）

| 范围 | 题数 | 云端成本 |
|---|---:|---:|
| canary | 137 | 0.55 元 |
| 全量不含 D7 | 3934 | **1.51 元** |
| D7 长上下文 | 120 | **14.69 元** |
| 全量 | 4054 | 16.20 元 |

**D7 只有 120 题却占九成成本**——60 题需 128k-256k 上下文。
日常评测绕开 D7，一次 1.5 元。

跑付费端点会强制确认，输入非 `yes` 不产生任何费用。API 子进程自动带 `--yes`。

---

## 8. 接口（Web 界面在用）

```http
# 控制
GET  /api/health
POST /api/start      {"suite":"public","endpoint":"local"}
POST /api/progress
POST /api/stop

# 模型选择
GET  /api/targets
POST /api/targets    {"target":{...}, "api_key":"sk-..."}
POST /api/discover
POST /api/launch
POST /api/attach
DELETE /api/target/<id>

# 预设（runner/presets.py）
GET  /api/presets            预设清单 + 参数 + 两条实测序列 + 在跑状态（带秒级缓存）
GET  /api/presets?scan=1     强制重扫在跑状态
POST /api/presets/scan       同上，给界面的“重新扫描”按钮用

# 结果
GET  /api/leaderboard    含 runs / paired / weights
GET  /api/runs           217 条历史
GET  /api/run/<name>
```

**密钥只存内存**（`targets.set_runtime_key`），`targets.json` 只记环境变量名。

---

## 9. 已知问题（接手优先看这里）

### 9.1 必须知道的五个坑

**坑 1：端口僵尸进程**

这台机器端口回收异常：重启 API 可能留下僵尸进程，多个进程同时监听同一端口，
请求被最早的旧进程接走，表现为「改了代码页面不更新」。

```powershell
netstat -ano | findstr ":8770 " | findstr LISTENING   # 出现多行 = 有僵尸
```

处置：换端口。`--port 0` 已自动处理。`taskkill` 对这些进程报 Access denied。

**坑 2：长任务被回收**

在 shell 里起的 `python run.py` 会随 shell 退出被回收。
用 `run_in_background` 起的 harness 作业才能跑完（实测 D8 全量 1073 秒靠这个跑完）。

**坑 3：D7 长上下文成本**

120 题 14.69 元，占全量九成。跑之前先 `arena_cli.py cost`。

**坑 4：只改 `location.hash` 不重载文档**

`init()` 不会重跑，必须单独监听 `hashchange`，否则地址栏导航无效。

**坑 5：`sk-` 安全扫描会误报**

文档里的 `sk-xxxx` 占位符、`sk-local` 默认值、`anonymize.py` 的脱敏正则
都会被子串匹配命中。要按**真实密钥格式**（`sk-` + 20 位以上字母数字）匹配。

### 9.2 未完成项

| 项目 | 状态 | 说明 |
|---|---|---|
| 本地 D8 重跑 | 未做 | 云端已改题面，本地仍旧，不可比 |
| 本地 D6 重跑 | 未做 | 同上，判分器版本不同 |
| D2/D3/D5/D7/D9/D10 逐题核实 | ✅ 已做 | 见 `docs/audits/AUDIT-全维度.md`，无新缺陷 |
| public 套件云端实测 | 未做 | 判分器就绪，未跑真实数据 |
| SimpleQA/TruthfulQA 别名表 | 部分 | 手工表仅 7 条，覆盖有限 |
| 182 个结果无版本标记 | 待处理 | 早于版本机制，重跑后自动带上 |
| 预设速度覆盖 | 部分 | 21 个预设中，GUI 序列只有 7 个、目录序列 9 个，其余留空（宁缺不编） |

---

## 10. 下一步建议（按价值排序）

**1. 本地 D8 + D6 用当前判分器重跑（最高价值）**

这是唯一能回答「本地 vs 云端到底差多少」的动作。
当前所有本地/云端对比都因为版本不同而失效。

```bash
# 先起本地模型
llama-server -m <model.gguf> --ctx-size 131072 --n-gpu-layers 99 --parallel 1 --n-cpu-moe 30 -ctk q4_0 -ctv q4_0 -fa on --reasoning off --host 127.0.0.1 --port 8080

# 再跑（不花钱）
python runner/run.py --endpoint local --suite agent --no-resume
python runner/run.py --endpoint local --suite reliability --no-resume
```

**2. public 套件云端实测**（0.43 元）

验证公开基准判分器在真实数据上的表现，目前只有 mock 测过。

**3. 补齐剩下 14 个预设的速度实测**

21 个预设里，GUI 序列只覆盖 7 个、目录序列只覆盖 9 个，其余在界面上是「—」。
补测后「预设与速度」页才有完整对比。**测速要真跑，不许估算填数。**

---

## 11. 交接检查清单

接手新会话，**只跑一条命令**：

```bash
cd E:\AI_workspace\06_model-arena
python runner/handoff_check.py
```

它检查 8 项：模块编译 / 题库结构 / 文档命令 / 判分器版本 /
题库与套件 / 配置可解析 / 密钥不落盘 / 成本可估算。

```
结论：全部通过，可以开始开发。     <- exit 0
结论：N 项失败 -> ...              <- exit 1，先修再改代码
```

全部通过才开始改代码。

---

## 12. 关键文件地图

```
runner/run.py            评测入口，18 个 suite
runner/judge_version.py  分层版本号（core/lcb/public）
runner/targets.py        模型目标管理（attach/launch，密钥内存）
runner/launcher.py       引擎启动命令拼接
runner/cost.py           成本估算
runner/arena_api.py      Web API（控制面 + 模型选择 + 预设速度）
runner/arena_tui.py      终端仪表盘
runner/arena_cli.py      CLI 统一入口
runner/selfcheck.py      题库结构自检
runner/doccheck.py       文档命令自检
runner/handoff_check.py  交接检查（8 项，一条命令）
runner/audit_inv.py      盘点各维度可复核失败题
runner/audit_dim.py      用当前判分器重判某维度
runner/presets.py        预设清单：识别 + 参数 + 双实测序列 + 在跑状态
runner/gen_preset_speed.py  预设速度数据生成器（GUI 序列）
ui.html                  Web 界面（37.6 KB / 968 行 / 5 选项卡）
5-打开界面.bat           启动器（端口自动选）
```

判分器：
```
judge.py              D2/D3/D5 选择题与数学
reliability_judge.py  D8 幻觉与拒答
code_judge.py         D4 HumanEval
lcb_judge.py          D4 LiveCodeBench（stdin/stdout）
agent_judge.py        D6 工具调用
d9_judge.py           D9 实战
d9_exec.py            D9 执行类
chess_judge.py        D10 棋类
longctx_judge.py      D7 长上下文
public_judge.py       公开基准路由
ifeval_judge.py       IFEval 24 个检查器
aliases.py            开放问答别名匹配
```

文档：
```
docs/dev/HANDOFF.md        本文件（新会话只读这一份）
docs/dev/UI-V3.md          UI 设计与预设速度页说明
docs/dev/STRUCTURE.md      代码结构
docs/dev/ARENA.md          评测机制
docs/audits/AUDIT-全维度.md  判分缺陷审计完整报告
STATUS.md                  当前状态（实测数字）
```

## 13. D11 / D12 设计与四轮难度迭代（2026-11-06）

### D11 视觉：四类基础 + 四类难题

| 子类 | 题数 | 考什么 | 27B | 9B |
|---|---:|---|---:|---:|
| ocr / pattern / detail | 9 | 字形、计数、细分辨 | 100% | 93% |
| color / layout / bars | 22 | 颜色、空间、比较 | 100% | 84% |
| **dense** 密集计数 | 6 | 数 32 格里的方块 | **0%** | 17% |
| **occlusion** 遮挡 | 6 | 谁画在上层 | **50%** | 50% |

**总分：27B 79.1% / 9B 72.1%（目前唯一能分模型大小的维度）**

关键设计：dense 让 27B 也全错——它每次都答 24（按 8x4=32 估），**没真去数**。
这说明该类题卡在“视觉分辨率”这个真瓶颈上，而不是题目不公平（答案 23-28，范围窄，猜不中）。

### D12 压缩：四轮加难度，两模型都满分

| 版本 | 加了什么 | 9B | 27B |
|---|---|---:|---:|
| v1 | 6 事实 / 40 填充 | 100% | 100% |
| v2 | 18 事实 / 200 填充 / 5 同姓干扰 | 100% | - |
| v3 | 片段倒序 + 诱饵行 | 100% | - |
| **v4** | 目标项目两条矛盾记录（残缺在前、完整在后） | **100%** | **100%** |

**停在 v4 的原因（重要）**：再往下只有“把信息砍到无解”一条路。
v1/v2 曾这样试过——模型答“信息不足”是**正确行为**，却算 0 分，那测的是诚实不是保留能力（代码注释记着这两次教训）。

所以 D12 的定位是**退化检测器**：换 KV 精度 / 开投机解码 / 改上下文后跑它，
如果从 100% 掉下来，说明那次改动伤了能力。它测不出“谁更聪明”，但对**判分器坏没坏**极敏感——
最初 5 次全 0 就是判分器坏了。

### 本轮修掉的缺陷（都是实测撞出来的）

1. **D12 把“解释了别人”当成“背名单”判错**
   9B 正确答 PRJ-4580 由 沈专员负责，附注说“摘要里还有沈工程师别混淆”，旧规则看到 2 个其他负责人就判 roster。
   修法：配对完整时提及别人不影响正确性——那正是合理的排除论证。

2. **D11 的“圆圈”被画成方块**
   c.rect() 填了方块，于是“圆圈”和“方块”在图上一样，题目无从判断。改成逐行画真圆。
   教训：**生成的图必须肉眼看过**，否则会把“题目坏了”当成“模型不行”。

3. **榜单合并规则：小样本覆盖大样本**
   build_report.py 原来“后写覆盖先写”，一次 4 题冒烟测试能把 552 题真实结果顶掉（实测 KV17 D2 显示成 100%）。
   已改为**样本多优先 + 少于 10 题不入库**。

4. **文件名正则太窄，两个维度从未进榜**
   _20261105-060459-kv17-261105-0202 多段后缀解析失败 → D6/D8 整份被跳过；
   _merged 后缀同样失败 → D2 只剩未合并的 45.3%（连接失败被当答错，违反铁律 6）。

---

## 14. 一键评测 / 环境自检 / 分发包（2026-11-07）

这一轮的目标不是加维度，而是**让别人也能跑起来**。改完必须从干净环境实际验证 ——
下面 6 个 bug 全是"代码看着对、真跑就崩"，肉眼审查看不出来。

### 14.1 新增 `runner/run_all.py` —— 一条命令跑完所有维度

之前跑一轮完整评测要敲 11 条 `run.py`（每条都要写 `--endpoint` / `--suite` / `--preset-id`），
漏一条就是漏一个维度，而且**漏了不报错**，只是榜单上那一格显示「—」，
看起来像"这个模型没这项数据"，实际是"没人跑过"。

```bash
python runner/run_all.py --endpoint kvmem --preset-id KV17-Bonsai2T5-200K
python runner/run_all.py ... --only code,math     # 只跑指定维度
python runner/run_all.py ... --skip longctx       # 跳过某维度
python runner/run_all.py ... --limit 2            # 每维度两题做冒烟
python runner/run_all.py ... --dry                # 只打印计划
```

它按**先快后慢**的顺序跑（跑错配置时前几个维度几秒就出结果，不用等两小时才发现
endpoint 写错），每个维度收一张汇总表，**整维跳过会写明原因**，跑完自动出报告与榜单。

两个实测坑，都写进代码注释了：

| 坑 | 症状 | 修法 |
|---|---|---|
| 跳过维度 `accuracy=None`，打印处仍 `* 100` | `TypeError: unsupported operand ... 'NoneType' and 'int'`，**进程崩掉，排在后面的维度全没跑** | `(x["accuracy"] or 0.0) * 100`，一次性改了 11 处 |
| 成功判定只看进程返回码 | D12 没出分却被算进"有可判分结果" | 先判 `skipped`（设计内，不算失败）→ 再判返回码 → 再判题数 → 最后判能否算出准确率 |

D12 的 summary 用 `baseline_acc`（压缩前）而不是 `accuracy`，读错字段会显示「有题但算不出准确率」。

### 14.2 新增 `doctor.py` —— 拿到项目第一件事

```bash
python doctor.py        # 体检
python doctor.py --fix  # 缺的本机配置补成模板
```

查七项：Python 版本(要 3.10+) / 是否真零依赖 / 目录结构 / 题库规模 /
`config/machine.json`(已 gitignore) / 端点配置 / Windows 控制台编码。
只读不写，`--fix` 才会生成模板。

**踩过的坑**：`results/` 和 `cache/` 在 `.gitignore` 里，clone 或解包后本来就不存在。
第一版把它们当"必检项"，于是**每个新人第一次跑就看到一条 FAIL**，而那条 FAIL 是设计内的。
现在改成"输出目录缺失就自动创建"。

### 14.3 目录整理与分发包

| 变化 | 说明 |
|---|---|
| `USAGE/ARCHITECTURE/CONTRIBUTING/STATUS.md` → `docs/` | 根目录只留 `README.md` + `AGENTS.md` |
| 5 个 `.bat` 改名 | `1-自检测试.bat` → `start-1-selftest.bat` 等 |
| `docs/dev/HANDOFF.md` 路径同步 | `runner/doccheck.py` 的 `DOCS` 列表要跟着改，**这是唯一真读文件的地方** |

```bash
# 打包(排除 gitignored 的大件)
tar -czf model-arena-YYYYMMDD.tar.gz \
  --exclude=./results --exclude=./cache --exclude=./datasets/public \
  --exclude=./datasets/private --exclude=./datasets/_mined \
  --exclude=./datasets/fixtures/longctx/long --exclude=*/__pycache__ \
  --exclude=./config/machine.json --exclude=./config/models.json \
  --exclude=./config/profiles.json -C . .
```

**3.3 MB / 347 文件**（源码 21.7MB 未压缩，压缩后 3.3MB）。排除掉的都是可重生成的：

| 缺什么 | 大小 | 怎么补 |
|---|---|---|
| `datasets/public/` | 23.7MB | `python scripts/fetch_bench.py` |
| `datasets/fixtures/longctx/long/` | 61.4MB | `python scripts/gen_d7_long.py` |
| `cache/` | 295MB | 自动重建 |
| `config/machine.json` | — | `python doctor.py --fix` |

**独立分发副本**（与开发目录隔离，跑测不污染开发结果）：
`E:\AI_workspace\06_model-arena-dist`，含 `分发包使用说明.md`。

### 14.4 这一轮修掉的 6 个 bug（全部是"真跑才暴露"）

按发现顺序：

1. **`ck.append(rec)` —— Checkpoint 的接口是 `add()`**
   `suite_public` 与 `suite_lcb` 两处写成 `append`，一跑到这两个维度就
   `AttributeError: 'Checkpoint' object has no attribute 'append'`。
   其他 9 个 suite 都是 `ck.add(rec)`，只有这两处写错 —— 说明是复制粘贴时漏改。

2. **跳过维度 `accuracy=None` 仍 `* 100`（11 处）**
   只修 D11 那一处不够，public/lcb 的打印同样会崩。批量修完才干净。

3. **我给缺文件保护写 `return None`，但调用方按二元组解包**
   `s, recs = suite_public(...)` → `TypeError: cannot unpack non-iterable NoneType`。
   **修 bug 引入了新 bug**，比原来的 `FileNotFoundError` 还难懂。
   跳过要返回 `(summary, [])`，summary 带 `skipped=True` + `skip_reason`。

4. **跳过 summary 缺 `elapsed_s`**
   打印汇总时 `KeyError: 'elapsed_s'`。教训：**别手写 summary 字段**，
   要么复用现有构造器，要么把字段列全。

5. **自检 bat 的注释与代码不符（根因）**
   注释写「跑 smoke + speed」，代码写 `--suite all`。
   `all` 会连 `public`(2037 题) 和 `lcb` 一起跑，而 `datasets/public/` 在 gitignore 里 ——
   **刚解包的人自检必然崩，而且崩在很后面才发生，看起来像"项目坏了"**。
   改成逐个套件循环，不含 public/lcb。

6. **`--suite` 是单值参数，我却写成逗号分隔多值**
   `invalid choice: 'math,mcq,instruction,...'`。改成 bat 里 `for %%S in (...) do` 逐个跑。

### 14.5 判分器回归测试 `runner/test_judges.py`（29 项）

之前 89 个模块只有 1 个真测试（`test_judge_mcq.py` 199 行）。
**判分器没有回归测试 = 谁改了都不知道坏没坏**，而铁律第 3 条正是"判分器比模型更容易出错"。

新增覆盖 4 组：

| 组 | 测什么 |
|---|---|
| D12 判分 | 完整答案判对 / 拼接式判对 / **提到别人仍判对** / 编造判错 / 只答姓氏判错 |
| D11 视觉 | 题数≥40 / 8 子类齐全 / **每题自洽(答案能判对)** / 乱答判错 |
| D4 LCB | **无 `{cases!r}` 占位符残留** / 接口统一 / 空答案不崩 |
| 题库规模 | 9 个维度题数与文档一致 |

```bash
python runner/test_judges.py   # exit 0 = 全过
```

**验证它真的有用来着**：注入一个假 bug（把配对判断改成 `if False:`），
测试必须 exit 1。第一次注入了却没报错 —— 查下去发现
`compression.py` 里有**两个同义分支**，第 208 行的
`if has_surname and has_role and len(named_others) <= 1` 先返回了，
第 221 行那段"提到别人不算背名单"的修复**永远走不到（死代码）**。
删掉重复分支后注入才生效。

> **教训**：修完判分器要**注入 bug 验证测试能抓到**，
> 否则"29 项全过"可能只是因为测试根本没覆盖到那行。

### 14.5b 第二批判分器测试 `runner/test_judges_extra.py`（99 项）与一个真 bug

第一批只覆盖 4 个判分器，而 `runner/` 下有 9 个。第二批补齐剩下 6 个：

| 组 | 断言 | 钉住什么 |
|---|---|---|
| D10 chess | 14 | 四子类自洽 / `answer:` 行优先于题面 `e2e4` 举例 / `Answer:**` 空行跳过 / 起点格不算目标格 |
| D7 longctx | 11 | 编造编号被抓并列进 `fabricated` / 跨文档矛盾必须全中 |
| D9 d9_judge | 11 | 5 种 check 语义 + **32 条 baseline 全自洽** |
| D6 agent | 10 | 缺调 / 越权 / 参数错 / 「信息不足却谎称完成」 |
| D8 reliability | 15 | 「不能撕破脸」绕开拒答 / 更名施压信 / 注入泄露与复读 / 编造 / 自信的错误 |
| D5 ifeval+checkers | 22 | no_comma 真会拦人 / JSON 字段名错时判错 / MCQ 字母数字括号 / **空规则集与畸形检查项** |
| public 路由 | 13 | 9 基准 scoring 标签不重复 / 代码题正误实现 |
| 版本指纹 | 3 | **所有判分器文件都进 `judge_version`** |

```bash
python runner/test_judges.py        # 29 项
python runner/test_judges_extra.py  # 99 项
```

#### 第二批抓到的真 bug：空规则集被当成满分

写测试时先做一次探针，结果是 **`run_checkers(None)` 返回 `(True, [])`** ——
`all([])` 是 `True`。而 `D5_full.jsonl` 里 300 条 `public-ifeval` 只带
`expect.instruction_id_list`（该由 `ifeval_judge` 判），**没有 `checkers`**。
`suite_instruction` 却无条件调 `run_checkers`，于是这 300 题**任何回答都判对**。

录在盘上的证据：

```
results/D5_instruction_queue_20261105-053607-... .json
  public-ifeval  correct=300/300   checker_results=[]     ← 空规则集给的满分
```

用当前判分器重算同一批回答：

| | 原记录 | 重算后 |
|---|---|---|
| D5 总分 | 397/401 = 99.0% | **310/401 = 77.3%** |
| public-ifeval | 300/300 = 100% | **213/300 = 71.0%** |

**22 个百分点的差距，全部来自「没查也算过」。**

修法（三处）：

1. `checkers.run_checkers`：空列表 → `(False, [no checkers defined])`。
   空规则集是**失败**，不是通过。
2. `run.py` 新增 `_judge_d5(row, text)`：**按 `expect` 形状分派** ——
   有 `instruction_id_list` 走 IFEval，有 `checkers` 走本项校验器。
   `suite_instruction`、`_judge_for`、`rescore.py`、`retry_wrong.py` 四处统一走它。
3. `checkers.check_one`：缺 `value` 的畸形检查项判错并说明原因，
   不再抛 `KeyError` 打挂整轮跑测（实测 `type=contains` 无 value 会崩）。

#### 顺带修掉的第二个 bug：改判分器不改变版本号

`judge_version.py` 的 `CORE_FILES` 里**没有 `checkers.py`**。
所以上面那个修改让 D5 翻转 300 题，而 `judge_version()` 一字未变 ——
唯一用来发现静默重算的控件看不见它。

现在把 `checkers.py` 加进 `CORE_FILES`，并加一条测试断言
「磁盘上每个 `*judge*.py` 都必须在指纹列表里」。

- 修复前 core 指纹：`e33116660747`
- 修复后 core 指纹：`e662da3dfd2e`

**所以这次的历史数据必须重算**（D5 的 `_rescored` 文件已生成）。

#### 注入验证

把这行改回 `return True`，`test_judges_extra.py` 立刻 **exit 1**：

```
FAIL checkers 空列表判错
FAIL checkers None 判错
```

恢复后 exit 0（99/99）。**这条测试确实覆盖到那一行**，不是摆设。

### 14.5c 第三批：`rescore.py` 生成的「什么都没重算」文件（真 bug）

前两个 bug 让 D5 分数虚高；这个 bug 让**修复无法生效**。

`LEVEL_*` / `run_*` 文件是**一次跨多维度的跑测**，它们没有 `summary["dim"]`，
每条记录自带 `dim`。旧 `rescore.py` 却用 `summary["dim"]` 去拼题库路径：

```
datasets/full/None_full.jsonl      ← 不存在
→ 每一条 q 都是 None → 1949 条全部 continue
→ 但仍然写出 "<name>_rescored.json"
```

结果：`_rescored` 文件的判决与原件**逐条相同**，却带着「已重算」的名字。

实测它掩盖了什么：

```
LEVEL_full_deepseek-flash_20261101-023045-cloudfull.json
  D5  by_dim = {n: 401, correct: 401}     ← 空规则集给的满分
  D5 记录 401 条, checker_results 全空
  _rescored 版本：同样 401/401            ← 假重算
修好后重算：366/401
```

榜单上 deepseek-flash 的「指令 D5」因此一直显示 **100.0%**。

修法（三处）：

1. `summary["dim"]` 缺失时按 `MULTI` 处理：把 `records` 里出现过的所有 dim 的题库
   合并成一个 `rows`，再**按每条记录自己的 `dim` 分派判分器**。
2. 补上 `D7` 分支（`judge_longctx`）。之前缺它，任何含 D7 的多维度文件都会撞到
   「维度暂不支持」而整份跳过 —— 正是这条让上面那个 bug 长期不可见。
3. **一条都没重算就不写文件**，返回 `skipped`；`main()` 打印跳过原因。
   多维度文件的 `by_dim` 也要重算，否则它与 `correct/n` 互相矛盾。

回归测试（`test_judges_extra.py` 新增 3 项）：

- 无可重算记录时不写文件
- 无可重算记录时不产生 `_rescored.json`
- `rescore_one` 里有 D7 分支

### 14.5d 全量重算后的真实变化

191 个旧的 `_rescored` 已移入 `results/_stale_rescored/`（它们不是重算结果，是旧判决的副本），
然后用修好的工具重算 417 个原文件，生成 159 个新的 `_rescored`。

翻转统计（84 个文件有变化）：

| 维度 | 对→错 | 错→对 |
|---|---:|---:|
| D5 | 227 | 0 |
| D6 | 67 | 21 |
| D2 | 56 | 2 |
| D8 | 32 | 42 |
| D4 | 24 | 11 |
| D10 | 0 | 8 |
| D3 | 0 | 4 |
| D7 | 1 | 1 |

D5 全是单向「对→错」—— 因为修掉的正是「没查也算过」。

榜单前后：

| 模型（预设） | D5 修前 | D5 修后 |
|---|---|---|
| deepseek-flash | 100.0% (401/401) | **91.3% (366/401)** |
| Qwen3.6-35B-APEX | 99.8% (400/401) | **83.5% (335/401)** |
| kv17-bonsai2t5 | 99.0% (397/401) | **77.3% (310/401)** |

### 14.5e D11 可重算，D12 必须拒绝重算

`rescore.py` 之前对 D11/D12 一律报「维度暂不支持」。补这两个维度时发现，
它们**能重建的程度完全不同**，不能用一个规则套。

**D11 视觉 —— 安全，已补上**

题目不是题库文件，是 `vision.build_all()` 按代码生成的图。测过：
对两个不同目录重建，43 条题的 **id 与答案完全相同**。所以按 id 查回
重建好的题就能重算。结果：`D11_vision_q9b_...d11q9b.json` 43 题 → 31/43 = 72.1%，翻转 0 题。

**D12 压缩 —— 不安全，必须拒绝**

D12 的 id 是 `d12_<seed>`，看起来能从 seed 重建场景。**实测不行**：

```
d12_20261101
  存下来的 reason: owner 石工程师 reconstructed
  现在重建的场景:   江总监
```

原因是 `build_scenario` 后来加了「同姓干扰」那一段（为了让模型必须分辨
而不是拼接），随机序列整体位移，**同一个 seed 不再产生同一个场景**。

如果不查这一点直接重算，会得到：

```
D12_compression_kvmem_...d12fix.json  重算后 0/48 = 0.0%  翻转 48 题
```

**48 个翻转全是假的** —— 模型答的是当年那份文档的项目，判分器却拿新场景去比对。

所以 D12 分支现在**直接拒绝**，并说明「只能重跑模型」。
拒绝发生在查表之前，否则会误报成「缺少题库行」，把一个已知原因伪装成未知。

**顺带修掉的错误标签**：`records` 为空的文件（实测 6 个 D11 空跑）原本报
「维度暂不支持重算」，现在报「文件里没有 records」—— 两者需要做的事完全不同。

回归测试新增 3 项：「rescore 支持 D11」「D12 拒绝离线重算」「无可重算记录时不写文件」。

> **通用教训**：判分器可以离线重算的前提是**题目可复现**。
> 题目由随机种子生成时，生成器一改，历史结果就与重算输入脱钩。
> 这类维度只能重跑，任何"重算"都只会产出看起来很像数据的假分数。

### 14.6 接手时注意

- ⚠ **2026-11-08 修掉的两个判分器 bug 让 D5 历史数字作废**。
  `results/` 里 D5 的 `*_rescored.json` 才是当前判分器下的正确数字
  （D5 总分 99.0% → 77.3%）；不带 `_rescored` 的旧文件保留但不要拿来做结论。
  core 判分器指纹已从 `e33116660747` 变为 `e662da3dfd2e`，跨指纹的分数不可直接比较。

- `config/machine.json` / `profiles.json` / `models.json` 是**本机配置**，不进版本库。
  doctor 会提示；`--fix` 从 `*.example.json` 生成模板。
- 预设（`profiles.json`）里 `Qwen3.6-35B-APEX` / `Qwen3.6-35B-UD` / `Qwen3.8-27B`
  三条的 `model_path` **指向不存在的文件**（模型已不在盘上或换了目录）。
  用户决定"不管"；跑测时它们会报模型不存在，属预期。
- ⚠ `results/_stale_rescored/` 里的 191 个文件**不是**重算结果，是旧判决的副本，
  留着只为追溯。当前可用的是 `results/*_rescored.json`（159 个，用修好的工具重算过）。
- ⚠ **D12 的历史结果不能离线重算**（场景由 seed 生成，生成器改过，同 seed 不再
  复现同一场景）。要更新 D12 必须重跑模型，不能跑 `rescore.py`。
- `C1-Cyber-35B-OPTIMIZED-32K` 用的是 `strata.exe`，那是**离线批量生成器**
  （`strata generate --tokens ...`，收预分词后的 token ID），**没有 HTTP serve 模式**，
  所以无法用现有测速流程测。且该 GGUF 缺 PLE 表（strata 架构必需），
  实测报 `per_layer_token_embd.weight is not in ...gguf`。
- 旧题目集跑出的历史结果（D3 436 题 / D4 80 题，现题库是 351 / 117）
  **仅作项目测试产物，不参与能力评价**，后续一律重跑。
  `results/batch_report.md` 顶部已有明确声明。

