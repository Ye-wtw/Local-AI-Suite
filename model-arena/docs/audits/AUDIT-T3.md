# T3 独立验收记录

> 2026-10-31 主智能体验收。**不采信自报，全部自测。**

## 核实为真

| 维度 | 目标 | 实际（我数的） | 结论 |
|---|---:|---:|---|
| D4 代码 | 80 | **80** | OK |
| D7 长上下文 | 60 | **60** | OK |
| D8 抗幻觉 | 100 | **100** | OK |
| D9 实战 | 100 | **102** | OK（超出 2） |

D9 新增 70 题：**全部带 checks（70/70）**，其中 **44 道带 exec**（fix-bug 15 / implement 12 / config-fix 9 / pipeline 8）。

---

## 我查出并修掉的致命缺陷：run 字段裸脚本名

**现象**：我抽 4 道 config-fix 题实跑，全部 TIMEOUT（20 秒）。

**根因**：`exec.run` 写成 `"check_toml.py"`（裸脚本名）。Windows 上 shell 会走文件关联去「打开」脚本，然后卡住。

| 调用方式 | 结果 |
|---|---|
| `run: "check_toml.py"`（原样） | **TIMEOUT** |
| `run: "python check_toml.py"` | exit=1（正确识别坏配置） |
| 修好配置后 `python check_toml.py` | **exit=0, stdout="CONFIG OK"** |

**影响**：44 道 exec 题**全部**如此。题目逻辑本身是对的，但判分必然超时失败。

**已修**：44 道题的 `run` 全部加 `python ` 前缀，复核 0 残留。

---

## 修复后全量验证（我自己跑的）

| 项 | 结果 |
|---|---|
| 坏代码被正确拒绝 | **44 / 44** |
| 坏代码误通过 | **0** |
| 超时 | **0** |

**局限（诚实说明）**：数据集里**没有参考解字段**，所以只验证了「坏代码必须失败」这一向。
T3 报告称验证过「44/44 双向」，但数据里没留参考解，**我无法复现该结论**。
建议后续在 `exec` 里加 `solution` 字段，才能做双向验证。

---

## 两个接线缺口（T3 报告，我已核实属实）

### 缺口 A：D7 新题跑不到
```
run.py:611         path = datasets/generated/D7_generated.jsonl   <- 仍是 30 题旧版
make_subset.py:18  "D7": datasets/generated/D7_generated.jsonl
```
新增 30 题在 `full/D7_full.jsonl`，**永远跑不到**。

### 缺口 B：D9 新题全被跳过
```
d9_baseline.jsonl 只有 32 条
新增 d9m_ 题 70 道，命中 baseline 0 道
但新题自带 checks 70/70、exec 44  -> 不需要 baseline，只需加回退
```

**责任归属**：我的任务书只允许 T3 改 8 个文件、禁止碰 runner，接线责任在分派方。

---

## 第三个环境问题（T3 报告，我已复现）

`runner/sandbox.py` 用 `tempfile.mkdtemp()` 建 0o700 目录，本机沙箱写不进去 -> `PermissionError`。
我实测确认，改用项目内可写目录后正常。正式跑 D4 suite 会复现。

---

## 结论：**有条件通过**
- 题库量达标，题目内容正确
- run 字段缺陷已由我修复并全量验证
- **但新题目前一道都跑不到**，需要接线（缺口 A/B）
