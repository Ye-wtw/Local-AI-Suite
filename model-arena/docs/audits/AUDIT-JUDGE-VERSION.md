# 判分器版本机制

> 2026-11-01。解决一个会让整个榜单失去意义的问题。

## 问题

判分器会改。改一次，**之前所有分数就失去了意义**——但没有任何标记说明这一点。

`build_report.py` 原来的合并逻辑：
```python
m["dims"][d] = v    # later stamp overwrites -> latest wins
```

**「时间戳最新者胜」**。这假设了新文件一定用了新判分器，但事实不是：
- 旧数据的时间戳可能更新（重跑同一批题）
- 新判分器可能只在内存里复判，没生成文件

结果：**榜单发布的是任何当前判分器都不会产生的数字。**

## 本会话的代价

一轮跑测改了 5 个判分器：

| 判分器 | 改动 |
|---|---|
| `reliability_judge` | 幻觉题长度规则、拒答交付规则、`_has_any` 中文匹配 |
| `code_judge` | SQL 参考解 JOIN 列 |
| `d9_exec` / `run.py` | exec 题面拼文件、判分分支查题目行 |

**241 个历史维度全部失去可比性**，且此前无人知道。

---

## 方案：源码哈希当版本号

`runner/judge_version.py`：

```python
JUDGE_FILES = ["judge.py", "reliability_judge.py", "code_judge.py",
               "d9_judge.py", "chess_judge.py", "longctx_judge.py",
               "d9_exec.py", "agent_judge.py"]

def judge_version():
    h = hashlib.sha256()
    for name in JUDGE_FILES:
        h.update(name.encode()); h.update(open(path, "rb").read())
    return h.hexdigest()[:12]
```

**为什么用哈希而不是手写常量**：改判分器时没人会记得改常量。哈希自动跟随源码变化，
改一个字版本就变。当前版本：`6696c10c96fa`。

---

## 三处接入

### 1. 保存时自动打标（`run.py` `save()`）
```python
payload["judge_version"] = JV.judge_version()
```

### 2. 合并时核对（`build_report.py`）
```python
jv = r.get("judge_version")
if CURRENT_JV and jv != CURRENT_JV:
    stale.append({...})
```

**关键决策：缺失版本号也算不匹配。** 没有版本不等于没问题——它意味着这个文件早于版本机制，
**没人知道是哪个判分器产生的**。把它当合格，最不可信的数字反而会活下来。

### 3. 报告里分级提示
```
!! 判分器版本核对（当前 6696c10c96fa）
   由更早版本的判分器产生，数字不可比:
     deepseek-flash  D8  旧=a1b2c3d4e5f6
   241 个维度没有版本标记（早于版本机制引入）:
     Qwen3.5-9B, Qwen3.6-35B-APEX, deepseek-flash
   重跑后自动带上版本号: python runner/run.py --endpoint <ep> --suite <suite>
```

旧版本逐个列出（需要知道具体哪项过期），未知版本按模型汇总（数量太大，逐条会淹没重点）。

---

## 对用户意味着什么

1. **看到分数前先看版本**。版本不一致的分数不能放进同一张表。
2. **改判分器后必须重跑受影响维度**，否则榜单会继续显示旧数字并附一行警告。
3. **首次运行的模型天然带正确版本**，不需要额外操作。

## 局限

- 哈希只覆盖 8 个判分文件。改题库（`datasets/`）不触发版本变化——题目变了但判分器没变，
  这时旧分数同样失效，当前机制**检测不到**。
- 版本号不记录「改了什么」，只记录「不一样」。要看差异得查 git。