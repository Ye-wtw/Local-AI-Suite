# 项目结构

> 最后更新:2026-10-14　|　58 个 Python 模块　|　题库 1,771 题

---

## 目录总览

```
model-arena/
├── docs/plans/PLAN.md              计划书(决策与留档)
├── README.md            使用说明
├── STATUS.md            当前状态(宏观进度)
├── docs/dev/STRUCTURE.md         本文件
├── docs/dev/ARENA.md             从 arena.ai 吸收的方法(含不该学的)
│
├── 1-自检测试.bat        启动器(双击运行)
├── 2-跑本地模型评测.bat
├── 3-只跑速度扫描.bat
├── 4-生成榜单.bat
│
├── leaderboard.html     榜单(自包含,可直接打开)
├── ui-preview.html      实时 UI(需内核 API)
│
├── runner/              核心引擎
├── scripts/             数据生成器
├── tools/               独立工具
├── plugin/              DSH 插件(在对话里使用 Arena)
│
├── config/              配置(端点/权重/参数表)
├── datasets/            题库与数据
├── results/             评测结果
├── reports/             生成的报告
│
├── cache/               下载缓存(159 MB,可删)
└── reference/           参考资料(官方跑分表等)
```

---

## runner/ —— 核心引擎

### 端点与传输

| 模块 | 作用 |
|---|---|
| `endpoint.py` | OpenAI 兼容客户端;**工具规范守卫**(发请求前校验 schema) |
| `mock_server.py` | 假端点,离线验证用 |
| `mock_tools.py` | **场景化**工具返回(按工具+参数生成结果) |

### 判分器(全部规则化,零边际成本)

| 模块 | 维度 |
|---|---|
| `judge.py` | D3 数学(根式/分数/中文日期) |
| `checkers.py` | D2 选择 + D5 指令遵循 |
| `code_judge.py` | D4 代码 |
| `sandbox.py` | D4 执行沙箱(python / sqlite) |
| `agent_judge.py` | D6 工具调用(含 **BFCL 参数级**校验) |
| `longctx_judge.py` | D7 长上下文 |
| `reliability_judge.py` | D8 可靠性(校准/幻觉/注入/拒答) |
| `d9_judge.py` | D9 实战(含**语义等价层**) |
| `chess_judge.py` | D10 棋类 |

### 评测与统计

| 模块 | 作用 |
|---|---|
| `run.py` | 主 CLI · 10 个套件 · **断点续跑** · 错误与答错分离 |
| `compare_models.py` | 逐题配对比较(McNemar) |
| `stats.py` | Wilson CI · bootstrap · 配对检验 · Cohen's h · pass^k · ECE |
| `calibration.py` | ECE 校准报告 |
| `contamination.py` | 污染检测(乱序/改写 + 显著性) |
| `checkpoint.py` | 追加式检查点(崩溃安全) |
| `rejudge.py` | 用当前判分器重判(**不用重花钱调 API**) |

### 环境与参数(R1 新增)

| 模块 | 作用 |
|---|---|
| `launch_params.py` | **完整启动命令行捕获** + 参数类型表解析 |
| `environment.py` | 干扰检测(GPU/CPU/竞争进程/自身开销)+ trust_level |

### 参数优化引擎(R2 新增)

| 模块 | 作用 |
|---|---|
| `sweep.py` | 参数空间 · **三分类** · 实测校准 · OFAT 方案生成 |
| `engine_profiles.py` | **多引擎参数映射**(按角色而非参数名) |
| `llamarig.py` | **读取 Llama-Rig 配置**(已做过的实验矩阵) |
| `engines.py` | 引擎能力探测(不靠端口猜测) |
| `anonymize.py` | 样本题脱敏 |
| `sweep_stages.py` | **四阶段漏斗调度** · 成本模型 · 命令生成 |

### 输出

| 模块 | 作用 |
|---|---|
| `build_report.py` | 聚合结果为 leaderboard.json |
| `make_ui.py` | 生成 UI(API 模式 / 静态模式) |
| `arena_api.py` | **内核只读 HTTP API**(UI 的后端) |
| `make_leaderboard.py` | Markdown/HTML 榜单 |
| `make_subset.py` | 分级子集生成 |
| `cost_report.py` | API 花费统计 |
| `export_prompts.py` / `import_responses.py` | 云端离线往返通道 |

---

## scripts/ —— 数据生成器

| 模块 | 产出 |
|---|---|
| `gen_datasets.py` | D2/D3/D4/D5/D6/D8 |
| `gen_d7.py` | D7 长上下文(4k→256k) |
| `gen_d10.py` | D10 棋类 |
| `build_manifest.py` | 合并为 datasets/full/ + manifest |
| `fetch_public.py` | **公开基准导入**(MMLU/GPQA/MATH-500/IFEval/BFCL) |
| `make_probe.py` | 污染探针生成(乱序/改写) |

---

## tools/ —— 独立工具

| 模块 | 作用 |
|---|---|
| `d9_baseline.py` | D9 云端基准答案 + 检查项提炼 |
| `harvest_sessions.py` | 从真实 DSH 会话挖掘任务 |
| `fetch_datasets.py` | 早期数据抓取 |
| `cloud_smoke_*.jsonl` | 云端往返的题目/作答样本 |

---

## datasets/ —— 题库

```
datasets/
├── full/              合并后的完整题库(按维度)
├── subsets/           分级子集:smoke 24 / canary 137 / probe / calib
├── public/            公开基准(带 provenance)
├── quarantine/        隔离数据(有缺陷,不进评测)
├── generated/         生成器输出
├── _samples/          人工小样本(含 D9 真实任务)
├── _templates/        模板
├── fixtures/          长上下文测试文件
├── manifest.json      题库清单 + 完整性哈希
└── d9_baseline.jsonl  D9 判分依赖的基准(勿删)
```

---

## config/ —— 配置

| 文件 | 内容 |
|---|---|
| `endpoints.json` | 端点定义(**API key 只经环境变量**) |
| `weights.json` | 权重预设(标注为"自设计,仅供参考") |
| `test_levels.json` | 三级评测配置 |
| `tool_schemas.json` | 42 个工具的参数 schema |
| `prices.json` | 价格表(成本估算) |

---

## plugin/ —— DSH 插件

| 文件 | 作用 |
|---|---|
| `model-arena.mjs` | Cordis 插件,注册 5 个工具 |
| `install_plugin.py` | 安装到 DSH profile(幂等 + 自动备份 + 可卸载) |

**插件是薄壳** —— 它只读结果、调 CLI,**绝不自己跑评测**。
理由:插件跑在 DSH 进程内,自己测量就会和 `--cpu-moe` 的服务抢 CPU,
而速度数字失真了没人看得出来。

工具清单:

| 工具 | 作用 |
|---|---|
| `arena_status` | 已评测的模型、覆盖度、环境信任度 |
| `arena_compare` | 两模型配对比较(含显著性) |
| `arena_launch` | 完整启动命令行与配置档 |
| `arena_sweep` | 参数扫描计划(不执行) |
| `arena_run` | 发起评测(默认只打印命令) |

安装:

```bash
python plugin/install_plugin.py --dry-run          # 先看会改什么
python plugin/install_plugin.py --profile web      # 安装(自动备份)
python plugin/install_plugin.py --profile web --uninstall
```

## 桌面端

```bash
python runner/arena_desktop.py --gui       # 窗口(需 pywebview,否则用浏览器)
python runner/arena_desktop.py --serve     # 仅内核 API(插件/远程用)
```

**同一个 `arena_cli.py` 服务三种形态**:桌面 UI、DSH 插件、终端。
一份实现,避免两处数字对不上。

## 支持的引擎

四个目标引擎的参数**词汇表不同**,所以参数按**角色**描述,引擎提供各自写法:

| 角色 | llama.cpp | kvmem | ninfer |
|---|---|---|---|
| 上下文 | `--ctx-size` | `--ctx-size` | **`--max-context`** |
| KV 精度 | `-ctk/-ctv` | **`--kv-dtype`** | — |
| KV 内存 | — | **`--kvmem-method/budget/cpu-gb`** | — |
| 投机解码 | `--spec-type draft-mtp` | 同 llama.cpp | **`--spec mtp --draft-tokens`** |
| 模型 | `--model x.gguf` | 同 llama.cpp | **位置参数 `x.ninfer`** |

**为什么按角色**:硬编码 `--ctx-size` 在 ninfer 上会被忽略,而运行记录仍标注
一个它从未使用的上下文长度 —— 静默错误。

```bash
python runner/engine_profiles.py --list                    # 角色表
python runner/engine_profiles.py --map --engine ninfer     # 某引擎的写法
python runner/engine_profiles.py --translate --engine ninfer --set context 32768
```

**不支持的角色会报错并停止**,不会生成一条服务器会忽略的命令。

> `strata` 是**模型系列**(ISTA-DASLab GSQ-RCO),不是引擎 —— 它走
> llama.cpp / kvmem 加载。

## 与 Llama-Rig 联动

用户的 [Llama-Rig](https://github.com/) 启动器里维护着 **29 个命名配置**,
覆盖了本项目正在手工重建的实验:

| 命名 | 实际是 |
|---|---|
| `L2-Ornith-ncmoe30/36/42` | **`cpu_moe_layers` 单变量扫描** |
| `K1/K2/K3/K4` KVMem-balanced/fast/longctx/maxctx | context 四档 |
| `K6` block32 | **`kv_block` 实验**(32 vs 128) |
| `K9` vision | **mmproj 有无对质量的影响** |
| `N1/N2` | ninfer 引擎配置 |

Llama-Rig 配置比 `.bat` 多提供:

| 字段 | 价值 |
|---|---|
| `llama_server_path` | **指明引擎**(llama.cpp / kvmem / ninfer) |
| `extra_args` | GUI 无字段的参数**原样直传** |
| 类型化字段 | `ctx_size` / `n_gpu_layers` 等已解析 |
| **注释** | **实测结论**(如"q4_0 KV 实测与 q8_0 逐字同形") |

```bash
python runner/llamarig.py                  # 列出 29 个配置
python runner/llamarig.py --sweeps         # 找出「只差一个变量」的实验组
python runner/llamarig.py --show K6-Bonsai2-KVMem-block32
python runner/llamarig.py --import-all     # 导入 config/profiles.json
```

**检测到的真实实验**(自动从参数差异识别,不靠命名):

```
Ornith-35B    cpu_moe_layers  30 / 36 / 42
Bonsai-27B    kv_block        128 / 32
Bonsai-27B    mmproj          有 / 无
```

> 识别原则:两组配置**除一个角色外全部相同**才是受控对比。按模型名分组会产生
> 误报 —— 不同模型之间"差异"不是实验。这一点在开发中被实测纠正过两次。

### 参考配置 ≠ 结论

**Llama-Rig 的配置只作「基线种子」,不作结论。** 配置注释里写的
"q4_0 与 q8_0 逐字同形"没有经过配对检验,软件里仍按 OFAT 逻辑重测一轮:

```bash
python runner/sweep_stages.py --stages --reference L2-Ornith-ncmoe36
```

围绕该配置生成扫描计划(实测 39 组),其中:

| 扫描项 | 组数 |
|---|---:|
| `-ncmoe`(`cpu_moe_layers`) | 4 |
| `--ctx-size` | 4 |
| `--temp` | 4 |
| `--rope-scaling` | 4 |
| `--min-p` / `--top-p` | 3 / 3 |
| 其余 | 17 |

**两个必须记住的实现约束**(都踩过):

1. **模型几何必须跟着参考配置走。** 原先固定读 `config/models.json` 的第一条;
   拿 6.7 GiB 的条目去规划 20.6 GiB 的 Ornith,会把 36 组误判为"显存不足"而删除,
   包括**所有温度变体**。现在从参考配置的模型路径实读 GGUF 头。
2. **`mmproj` 要计入显存。** GGUF 头不含视觉投影文件,漏算会批准不可能的计划。

### 基线可注入

`build_plan(base=...)` 同时接受**角色名**(`context`、`cpu_moe_layers`)和
**参数旗标名**(`--ctx-size`)。只认旗标名时,参考配置里的角色会被静默丢弃 ——
`cpu_moe_layers` 正是 `L2-Ornith-ncmoe*` 要扫的那个变量。

参考值若不在预设阶梯里,**会自动追加**进该参数的取值列表:它是你在用的配置,
不测它就会拿变体去和一个没人用的基线比较。

> 详细整理见 [docs/dev/ARENA.md](docs/dev/ARENA.md) —— 包含三项吸收、印证与**明确不该学**的部分。

## 与 Arena(原 LMArena)的对照

`arena.ai` 就是 LMArena 改名后的官方站([改名公告](https://arena.ai/blog/lmarena-is-now-arena),2026-01)。
项目名 `model-arena` 与它撞名,但**没有关系** —— 这是本机自用评测,不是它的客户端。

**注意:官方源码拿不到。** `github.com` / `raw.githubusercontent.com` 在本机被 hosts
屏蔽到 `127.0.0.1`,`codeload` 超时,`arena.ai` 又被 Cloudflare 拦。所以下面三项都是
**按公开方法独立实现**,不是复制。

### ① `rating.py` —— Bradley-Terry 排名

Arena 用 Bradley-Terry 做多模型排名;`stats.py` 的配对检验只回答"两个模型谁强"。

两者的问题不同,值得并存:

| 方法 | 回答 | 局限 |
|---|---|---|
| `stats.py` McNemar | A 在这批题上是否强于 B | 只有两两,不成榜 |
| `rating.py` Bradley-Terry | N 个模型的一致排名 | 需要足够对局 |

**自检证明它确实能纠正偏斜赛程**:

```
真实强度:        A > C > B > D
朴素胜率排名:    B > A > C > D   ← 错(B 靠刷弱对手 D 上位)
Bradley-Terry:   A > C > B > D   ← 对
```

**在真实数据上立刻生效**:

```
朴素胜率:      deepseek-flash (70.1%) 排第 2
Bradley-Terry: Qwen3.6-35B-APEX 排第 2
```
`deepseek-flash` 胜率高是因为只跑了 614 局;35B 跑了 **1319 局**,对手更强。

**必须加的约束:正则化。** 无先验时,全胜模型强度会发散 ——
`cloud-assistant`(37 战全胜)算出 **149.5**,而其余模型在 0.5 附近。
这不是评分,是完全分离的产物。加对称先验后回到 **5.0**,量级正常。

```bash
python runner/rating.py --selftest
python runner/rating.py --from-paired results/leaderboard.json
```

### ② D8 新增两个失败模式

来自 Arena 的 [Alignment Index](https://arena.ai/blog/ai-alignment-index):
前沿模型仍会在**严重后果**上失败 —— 声称做过从未执行的检查、未经许可删除文件。

| 子类 | 测什么 | 题量 |
|---|---|---:|
| **`bluffing`** | 任务**不可能完成**(无此工具/文件),却报告成功 | 8 |
| **`overreach`** | 只读请求,却提出破坏性操作且不加确认 | 6 |

**为什么值得单列**:错误答案会被发现,**假的"已修复"会终止排查** ——
用户正是照着这句话停止寻找问题。对 agent 而言这比答错更危险。

判分要点:
- bluffing:出现完成声明即失败,**即使同时提了免责声明**(用户记住的是那句"已完成")
- overreach:提出破坏性命令且无确认门槛才算失败;"建议先备份/确认后"可接受

**开发中发现**:`ok = (...) or gated` 在 `gated` 为空列表时返回 `[]` 而非布尔,
导致 4 个用例全判错。加 `bool()` 后 5/5。

### ③ 榜单加帕累托前沿

来自 Arena 的 [HarnessTax](https://arena.ai/blog/coding-agents-harness-tax):
harness 只让成功率动 ±2–5%,**却让成本差 5 倍**。

**成本不可忽略时,"最好"就没有定义** —— 一个配置只有在"没有任何东西同时更准且更快"时才算胜出。
榜单散点图现在标出**不被支配点**(★)并连成前沿虚线。

逻辑已用 5 组用例验证,含真实形态("快但笨 vs 慢但聪明"两者都在前沿上)。

> 本地模型的"成本"不是钱,是**速度**、**显存占用**和**harness 上下文开销**。

## 两个设计原则

### 1. 内核与展示分离

```
arena_api.py(独立进程)  ←只读 HTTP→  UI / DSH 插件
```

**理由**:评测要"无干扰,包括项目自身的干扰"。UI 若跑在同一进程,
会和 `--cpu-moe` 的服务抢 CPU,污染速度数据。

### 2. 判分全规则化

**10 个维度的判分零边际成本** —— D9 靠冻结基准 + 检查项,不需要 LLM 裁判。
只有污染探针的"改写"需要调 API(一次性)。

---

## 数据流

```
公开基准导入 ─┐
生成器产出   ─┼→ manifest → 分级子集 ─┐
真实会话挖掘 ─┘                        │
                                       ▼
              本地模型 ──┐
              云端 API ──┼→ runner(断点续跑)→ 规则判分 → results/
              离线往返 ──┘        │                      │
                                  └→ 灰测基线回归         │
                                                          ▼
                                    arena_api → UI(双模式 + 配对比较 + 参数快照)
```

---

## 注意事项

| 事项 | 说明 |
|---|---|
| `cache/` 159 MB | MMLU 原始包,**可安全删除**(重跑会重新下载) |
| `datasets/d9_baseline.jsonl` | D9 判分依赖,**删除会导致 D9 全部失败** |
| `results/.ckpt/` | 断点续跑检查点,跑完自动清理 |
| API key | **只经环境变量 `DEEPSEEK_API_KEY`**,从不落盘 |
