# 项目架构

> 面向改代码的人。使用方式见 `USAGE.md`，开发规则见 `AGENTS.md`。
> 本文所有数字来自实际盘点，不是设计意图。

---

## 一句话

**输入一个模型端点，输出一个可追溯的分数。**

```
题库 (2017 题)  ──┐
判分器 (9 个)     ├──► 逐题记录 ──► 判分器版本打标 ──► 榜单
端点 (本地/云端)  ──┘
```

---

## 目录结构

```
model-arena/
├── runner/           60 个模块，全部逻辑在这里
├── scripts/          21 个题库生成器（写生成器，不手改 jsonl）
├── datasets/         98.5 MB
│   ├── full/         正式题库，9 个 jsonl，2017 题
│   ├── subsets/      分级子集（smoke/canary/full...），由 full 生成
│   ├── public/       13 个权威基准（MMLU-Pro/GPQA/GSM8K...）
│   ├── fixtures/     测试夹具（视觉图、长上下文文档）
│   ├── variants/     实验变体（如 D8 加提示版），**不进正式题库**
│   ├── probe/        污染探针
│   ├── generated/    历史遗留，部分维度曾从这里读（已修正）
│   └── agent_env/    D6 工具环境
├── config/           12 个配置
│   ├── endpoints.json    端点定义（api_key_env 而非明文 key）
│   ├── test_levels.json  分级定义（题量 + max_ctx）
│   ├── weights.json      维度权重
│   └── machine/models/profiles.json  本机配置（gitignored）
├── results/          结果目录（gitignored，219 项 28.9 MB）
├── reports/          生成的报告
├── cache/            公开基准下载缓存（295 MB）
├── docs/             文档归档
│   ├── audits/       11 份实测审计（每个缺陷的取证）
│   ├── reports/      3 份测量报告
│   ├── plans/        2 份计划
│   └── dev/          4 份开发文档（HANDOFF/STRUCTURE/ARENA/README.dev）
├── plugin/           DSH 插件
├── tools/            辅助脚本
└── reference/        参考资料
```

---

## runner/ 的四个层次

### 第 1 层：判分器（9 个，被保护的）

**这一层决定了「分数」的含义，改动会作废历史数据。**

| 模块 | 维度 | 判什么 |
|---|---|---|
| `judge.py` | D2/D3 | 选择题、数学题的规则判分 |
| `checkers.py` | D5 | 可验证的指令遵循 |
| `agent_judge.py` | D6 | 工具调用决策 |
| `longctx_judge.py` | D7 | 长上下文针召回（配对判分） |
| `code_judge.py` | D4 | 代码：沙箱真跑 + SQL 等价性 |
| `reliability_judge.py` | D8 | 幻觉/校准/注入/拒答 |
| `d9_judge.py` + `d9_exec.py` | D9 | 实战：规则检查 + 真执行 |
| `chess_judge.py` | D10 | 棋盘题（FEN/一步杀/可达性） |
| **`judge_version.py`** | — | **对上面 8 个源码做哈希，产出判分器版本** |

**全部规则化，无 LLM 裁判。** 成本为零、结果可复现。

### 第 2 层：执行（4 个）

| 模块 | 职责 |
|---|---|
| `run.py` | 唯一的 runner，按 `--suite` 分派到各维度 |
| `endpoint.py` | OpenAI 兼容客户端（含墙钟 deadline、多模态） |
| `checkpoint.py` | 断点续跑，每题实时落盘 |
| `stats.py` | McNemar、置信区间、Bradley-Terry |

### 第 3 层：入口与工具（17 个）

| 模块 | 用户场景 |
|---|---|
| `arena_cli.py` | **命令行总入口**（status/compare/launch/sweep/env/run） |
| `arena_api.py` | 只读 HTTP API |
| `arena_desktop.py` | 桌面窗口（无 pywebview 则回退浏览器） |
| `setup.py` | 首次运行体检 |
| `selfcheck.py` | 题库静态自检（不调模型） |
| `rejudge.py` | 用当前判分器重算历史数据 |
| `build_report.py` | 汇总 results → leaderboard |
| `make_leaderboard.py` | 渲染单文件 HTML 榜单 |
| `make_subset.py` | 从 full 生成分级子集 |
| `abtest.py` / `abtest_report.py` | A/B 对照实验 |
| `compare_models.py` / `compare_runs.py` | 配对比较 / 回归检测 |
| `sweep*.py` | 参数扫描漏斗 |
| `make_ui.py` / `make_coarse.py` | UI 与粗筛题 |

### 第 4 层：支撑（30 个）

按用途分组：

- **环境探测**：`detect` `environment` `engines` `engine_profiles` `llamarig` `profiles` `launch_params` `serverctl`
- **新增维度**：`compression`（D12）`vision`（D11）`dyn_multi`（多轮）`chess_core`（棋规）
- **可信度**：`contamination` `calibration` `rating` `anonymize`
- **数据**：`parquet_lite`（纯标准库读 Parquet）`agent_env` `mock_tools`
- **辅助**：`mock_server`（假端点）`sandbox`（隔离执行）`speed` `speed_probe` `cost_report` `export_prompts` `import_responses`

---

## 依赖关系

实际引用次数（被几个模块 import）：

```
run              5  ← 被 CLI/API/桌面/abtest 共用
stats            4  ← 所有比较结论都走它
environment      4  ← 每次运行都记干扰
endpoint         3
engine_profiles  3
judge_version    3  ← run/save、build_report、rejudge
checkers         3
```

**没有循环依赖，没有单点巨石。** `run.py` 被引用最多但仍只是「调度器」。

---

## 一次评测的数据流

```
1. run.py --suite canary
      │
      ├─► test_levels.json 取 max_ctx → ctx gate 过滤（跳过高上下文题）
      ├─► subsets/canary.jsonl 读题（137 题）
      ├─► checkpoint 恢复进度（断了能续）
      │
2. 逐题循环
      │
      ├─► _prompt_for(row)  ← 按维度拼 prompt（D9 exec 在这里注入文件）
      ├─► endpoint.chat()   ← 墙钟 deadline + max_tokens 上限
      ├─► 分派判分器        ← D2→judge, D9→d9_exec, D8→reliability_judge...
      ├─► 写 checkpoint（每题 fsync，强杀最多丢一题）
      │
3. 汇总
      │
      ├─► save() 打 judge_version
      └─► results/LEVEL_<suite>_<ep>_<stamp>.json
      │
4. build_report.py
      │
      ├─► 扫 results/*.json
      ├─► 按 (endpoint, model) 合并，**同维度后写覆盖**
      ├─► 版本不符 → 进 stale 列表 → 打印警告
      └─► results/leaderboard.json + reports/leaderboard.md
```

---

## 三个关键设计决策

### 1. 为什么不用 LLM 当裁判

LLM 裁判有偏好（偏向自己、偏向长答案）、不可复现、有成本。
所有判分都是规则匹配或真执行。

**代价**：判据写法要求高，写不好会把正确答案判错——本项目实际发生过 7 次。

### 2. 为什么判分器要版本化

判分器一改，之前所有分数失去意义。用源码哈希当版本号：

- 自动跟随改动，不依赖人记得改常量
- 保存时打标，合并时核对
- 缺失版本 = 不可信（不是「没问题」）

`rejudge.py` 让历史数据能用新判分器重算，**不重跑模型，零成本**。

### 3. 为什么先跑 canary 不跑 full

| 级别 | 题量 | 用途 |
|---|---:|---|
| coarse | 20 | 参数扫描 |
| smoke | 24 | 验证通路（0.4 秒） |
| **canary** | **137** | **主力档，够做显著性检验** |
| full | 2017 | 最终定论（本地 5-10 小时） |

137 题的配对 McNemar 能检出 8pp 以上差异。full 只在需要精确数字时跑。

---

## 加一个新维度要动哪里

```
1. runner/<dim>.py          造题 + 判分 + 统计（三件事）
2. runner/run.py            suite_<dim>() + 注册 --suite choices + 汇总打印
3. runner/make_subset.py    SOURCES 加一行（若题目在 datasets/full）
4. runner/selfcheck.py      PROMPT_FIELD / CHECK_FIELD 加映射
5. runner/judge_version.py  JUDGE_FILES 加文件名（若判分在同文件）
```

**第 4、5 步容易漏**，漏了会让自检误报或版本不更新。

---

## 已知结构性问题

| 问题 | 影响 | 状态 |
|---|---|---|
| `datasets/generated/` 仍有旧切片 | 曾导致 full 只跑 559/2017 | 已修（SOURCES 指向 full），目录未删 |
| 版本机制不覆盖题库改动 | 改题不改判分器时，旧分数失效但检测不到 | **未解决** |
| `results/` 里混着旧版本数据 | 榜单显示 unknown 版本警告 | 需重跑或重判 |
| 各维度 schema 不统一 | 自检必须维护字段映射表 | 设计如此，已文档化 |
