# 多轮动态轴 · 实现与验证记录

> 2026-10-31。来源：两条视频指出的「静态考卷测不出真实能力」。这是 PLAN-v2 附二「修订 2」的落地。

## 为什么加这一轴

其余所有 suite 都是「问一次、读一个回答」，测单次作答。但编码 agent 是分轮工作的：改 → 跑测试 → 读失败 → 再改。

| 模型 | 一轮修好 | 三轮修好 | 永远修不好 |
|---|---|---|---|
| 单次判分 | 对 | **错** | 错 |
| 多轮判分 | 1 轮解决 | **3 轮解决** | 失败 |

**单次判分把前两种混为一谈。** 这是这一轴存在的理由。

## 设计

**复用不新造**：直接跑 D9 的 44 道 exec 题——它们已有小坏仓库（files）、运行命令（run）、成功判据（expect_exit/stdout）。

每轮喂回去的是**真实测试输出**，不是模拟反馈：退出码 + 真实 stdout + 真实 stderr。

## 四个报告字段（各回答不同问题）

| 字段 | 含义 | 为什么单列 |
|---|---|---|
| solved | 测试是否曾经转绿 | 最基本 |
| rounds_used | 第几轮解决 | 1 轮 vs 3 轮是不同的工具 |
| time_to_green_s | 到首次通过的真实耗时 | 本地模型慢但准是合法形态 |
| timed_out | 是否撞到时间上限 | **标注，不按 0 分计** |

超时处理遵循用户 2026-10-31 的判断：不该一刀切改成 0 分，设置时长限制、超时即标注，因为本地模型可能时长久但质量高。

## 验证（我自己跑的，用假端点）

| 场景 | 结果 |
|---|---|
| 第 1 轮就对 | solved=True, rounds=1 |
| **第 3 轮才对** | **history=[(1,False),(2,False),(3,True)]** ← 反馈真的喂回去了 |
| 永远修不好 | rounds=3, solved=False |
| 超时 | timed_out=True, 不是 0 分 |

**端到端**：`--suite dynamic --limit 2 --max-rounds 2` 跑通，产出 `results/DYN_multi_mock_<stamp>.json`，summary 含 n / solved / solve_rate / timeout_rate / mean_rounds / median_time_to_green_s。

## 用法

    python runner/run.py --endpoint <名> --suite dynamic
    python runner/run.py --endpoint <名> --suite dynamic --max-rounds 3 --task-timeout 300

## 成本（llama.cpp 实测速度推算）

| 轮数 | 单题耗时 | 20 题 |
|---|---:|---:|
| 1 轮 | 14.5 s | 4.8 min |
| 3 轮 | 43.5 s | **14.5 min** |
| 5 轮 | 72.5 s | 24.2 min |

占单配置 390 题静态预算（138 min）的 **约 11%**。

## 新增/改动文件

| 文件 | 说明 |
|---|---|
| runner/dyn_multi.py | 新增，多轮循环引擎 |
| runner/run.py | 加 --max-rounds / --task-timeout，注册 dynamic suite，加汇总打印 |

## 仍未验证

- **真实模型上的表现**（GPU 任务 0 次，等授权）
- 模型是否理解「提交完整文件」的指令（mock 不会改代码，全 fail 是预期）
- 长会话下的上下文增长（3 轮 × 真实测试输出，可能吃掉不少 token）