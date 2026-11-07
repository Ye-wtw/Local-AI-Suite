# 接线修复记录 · 2026-10-31

> T3 补的 164 道题此前**一道都跑不到**。本记录是打通接线的过程与验证。
> 责任归属：我的任务书只允许 T3 改 8 个文件、禁止碰 runner，接线责任在分派方。

## 缺口 A：D7 长上下文读错文件

```
run.py:611  generated/D7_generated.jsonl  (30 题旧版)  ->  full/D7_full.jsonl (60 题)
```

**验证**：D7_full 60 题中 57 道有 `context_file`，指向的 13 个 fixture 文件**全部存在（缺失 0）**。

**补题效果**：

| 档位 | 补题前 | 补题后 |
|---|---:|---:|
| <=16k | 14 | **30** |
| 17k-64k | 8 | **22** |
| 65k-128k | 4 | 4 |
| >128k | 4 | 4 |

**注意**：长档没变。128k 档仍只有 4 题，>128k 仍 4 题。T3 补的题集中在短档。

## 缺口 B：D9 baseline 缺记录导致全跳过

**问题**：`d9_baseline.jsonl` 只有 32 条，新增 70 道 d9m_ 题命中 0 条，`suite_realworld` 直接 `continue`。

**修复**：`_load_d9()` 加回退——题目自带 `checks` 或 `exec` 时，用题目行自身作判据。

## 缺口 C（新发现）：exec 题从未被执行

**问题**：即使 `_load_d9` 修好，`suite_realworld` 第 970 行仍是 `if not b.get("checks"): continue`，
exec 题没有 `checks` 字段，会被二次跳过；且原循环只做 `ep.chat` + `judge_d9`，**从不执行代码**。

**修复**：
- 新增 `runner/d9_exec.py`：提取模型代码块 -> 写入 `fix_file` -> 真跑 `run` 命令 -> 比对退出码与 stdout 标记
- `suite_realworld` 按 `b.get("checks") or b.get("exec")` 分流，exec 题走 `run_exec_task`
- 记录里加 `scored_by: exec|rules` 与 `exit_code`、`timed_out` 字段

**为什么不用 `sandbox.py`**：它用 `tempfile.mkdtemp()` 建 0o700 目录，本机沙箱写不进（PermissionError，已实测复现）。
新模块用 `makedirs` 在调用方给的 root 下建目录。

## 验证结果（我自己跑的，非采信子代理）

| 项 | 结果 |
|---|---|
| D9 可运行 | **102 / 102**（exec 44 + rules 58） |
| 坏代码必须失败 | **44/44 失败，0 误通过** |
| 空回答必须失败 | **44/44 失败** |
| 构造正解必须通过 | **correct=True, exit=0, marker ok** |
| 编译 | run.py / d9_exec.py 均 0 失败 |

## 仍未解决

1. **128k 档只有 4 题**，按 10/10/10 跑统计效力为 0%。需补题或改档位设计。
2. **`datasets/subsets/*.jsonl` 是旧快照**，未重建（`make_subset.py --all` 可重建）。
3. **真跑（非 mock）零验证**——GPU 任务 0 次，等授权。
