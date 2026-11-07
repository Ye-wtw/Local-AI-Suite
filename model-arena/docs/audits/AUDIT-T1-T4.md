# T1 / T4 独立验收记录

> 2026-10-31 主智能体验收。**不采信子代理自报，全部自测。**

---

# T4 · 判分器修正

## 自检复现
`python runner/test_judge_mcq.py` -> **PASS 29 / FAIL 0**，exit=0（我独立跑，非采信报告）

## 我的独立探针（不复用 T4 测试代码）

| 基准 | 结果 |
|---|---|
| gpqa 198 | ok=198 bad=0 |
| mmlu_pro 12032 | ok=12032 bad=0 |
| **arc 1172（含 22 数字标签）** | **ok=1172 bad=0** ← 唯一真实修复 |
| mmlu 285 | ok=285 bad=0 |

## 回归测试（12 个真实格式）
简短答案 / 带解释 / 英文 / 换行 / 纯字母 / 加粗 / 括号 / 长中文解释 / 带标点 / 小写 / 数字 / 数字长解释
-> **12/12 通过，200 字符窗口未吃掉任何真实格式**

## 副作用核查
- 53 个 runner 模块编译：**0 失败**
- `run.py` import：**OK**
- `judge_math`（D3）/ `run_checkers`（D5）/ `code_judge` / `reliability_judge` / `agent_judge`：**全部正常**

## 结论：**通过**

---

# T1 · 对照实验引擎

## 核实为真

| 项 | 结果 |
|---|---|
| 3 个新增文件 | abtest.py 31.2KB / abtest_report.py 11KB / abtest_plan.json 3.1KB |
| 既有模块未改 | run.py 10-13、endpoint.py 10-12、stats.py 10-11、checkpoint.py 10-12、speed_probe.py 10-13、engines.py 10-13、engine_profiles.py 10-14 —— **mtime 全部早于本次** |
| dry-run | exit=0，51 行完整输出 |

## 我查出并修掉的两个真问题

### 1. plan 里的模型路径指向一个「目录」

```
Attributes : Directory    <- 名字以 .gguf 结尾的目录，不是模型
```

真实模型在**里面一层**（HuggingFace 下载器常见的仓库名当目录布局）：
```
Qwen3.6 APEX/
└── ...-Compact.gguf/          <- 目录
    ├── ...-Compact.gguf       <- 真实模型 15.85 GiB
    └── mmproj-...-F16.gguf    <- 视觉投影
```

**后果**：dry-run 显示正常（只打印路径不验证），**真跑必然失败**。
**已修**：A/B 两臂路径改为真实文件，验证 `isfile=True, 15.85 GiB`。

### 2. D7 阶梯容量不足（比 T1 报的更严重）

| 档位 | 全库题数 | 计划 | 10pp 检出率 |
|---|---:|---:|---:|
| 16k 及以下 | 14 | 10 | **0%** |
| 64k 档 | 8 | 10 | **0%** |
| 128k 档 | **4** | 10 | **0%** |
| 256k 档 | 4 | — | — |

T1 说 128k 有 8 题，那是 **128k(4) + 256k(4) 合并**算的。真正的 128k 档只有 **4 题**。

**这不是 bug，是题库容量事实。** 但它意味着：按计划的 10/10/10 跑，
长上下文维度的统计效力是 **0%** —— 跑完也测不出差异。

**建议**：合并为两档 —— 常规档（<=64k，22 题）+ 超长档（>64k，8 题）。
或者按 PLAN-v2 补题（D7 -> 60）。

## 结论：**有条件通过**
- 引擎本身可用
- 但 D7 题量必须先补，否则长上下文维度白跑

---

# 本轮我犯的错（记录，不抹掉）

| # | 错误 | 真相 |
|---|---|---|
| 1 | 断言 MMLU-Pro 55% 假低分 | 测了零调用点的死代码 `judge.judge_mcq`；生产是 `checkers.judge_mcq`，12032/12032 全对 |
| 2 | `judge_mcq(row_dict, letter)` 报 AttributeError | 签名是 `(response_text, answer_letter, options)`，我传错参数 |
| 3 | `run_checkers(checkers, response)` 传反 | 参数顺序是「检查项在前，回答在后」 |
| 4 | dry-run 报 EXIT=-1 | PowerShell `Select-Object -First` 提前关管道杀掉进程 |

**共同模式**：都是在**没确认「哪个是生产实现 / 正确签名」**的情况下就下结论。
四次里有三次是调用方式错误，一次是查错对象。
