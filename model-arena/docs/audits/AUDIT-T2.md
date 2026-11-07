# T2 独立验收记录

> 2026-10-31 主智能体验收 T2（权威基准接入）。**不采信子代理自报，全部自测。**

## 核实为真的部分

| 项 | 结果 |
|---|---|
| 5 个 parquet 行数 | mmlu_pro 12032 / gsm8k 1319 / humaneval 164 / arc 1172 / truthfulqa 817，**逐个命中** |
| 抽样验真伪 | ARC 是真实科学题、HumanEval 是完整代码 prompt、TruthfulQA 是反直觉题，**无空串无乱码** |
| calib 校准锚点 | 198 题与 gpqa.jsonl 逐题比对，答案字母**不一致 0 处** |
| parquet 读取器 | 纯标准库，支持 SNAPPY + RLE_DICTIONARY + PLAIN |

---

## 更正：我昨晚报的「MMLU-Pro 55% 假低分」是错的

**错误结论**：我断言 MMLU-Pro 有 6627 题（55.1%）因判分器只认 A-D 而被判 False。

**实测推翻**（生产路径 `checkers.judge_mcq`，2 参数签名）：

| 基准 | 结果 |
|---|---|
| mmlu_pro 12032 题 | **ok=12032 bad=0（100%）** |
| gpqa 198 题 | **ok=198 bad=0** |
| arc 字母标签 1150 题 | **ok=1150/1150** |

**我的错误链条**：
1. 我用 `judge.judge_mcq`（3 参数）测试 —— 但 `run.py:17` 实际 `from checkers import judge_mcq`，**生产走 checkers 的 2 参数版**
2. `judge.judge_mcq` 在本仓库**零调用点**（死代码），它的正则确实只有 A-D
3. 我测了死代码，把结论套到了生产路径上

**教训**：验证「判分器能否消费」之前，必须先确认**哪个才是生产实现**。我跳过了这一步。

---

## 真实缺陷：ARC 数字标签 22 题全错

生产判分器 `checkers.judge_mcq` 的正则是 A-J，**不认数字**。

| 类型 | 题数 | 结果 |
|---|---:|---|
| ARC 字母标签 | 1150 | 全对 |
| **ARC 数字标签** | **22** | **0/22，extracted=None** |

数字标签题形如：`options={'1': 'Kinetic energy...', '2': '...'}`，`answer_letter='2'`。
正则抓不到数字 -> extracted=None -> 判 False。

**影响**：ARC 1172 题里 22 题（1.9%），造成约 **-1.9pp** 的系统性低估。不致命，但该修。

---

## 处置

- T4 已派发，原任务书写的是改 `runner/judge.py` —— **改错地方了**
- 已授权扩边界，改为修改 `runner/checkers.py` 的生产实现
- 建议消除 `judge.judge_mcq` 与 `checkers.judge_mcq` 的两份重复实现（死代码会被误用，本次就是活例）

## 副作用（已核实无害）

- `math500.jsonl` 300->500、`ifeval.jsonl` 300->541：原先被截断，现为全量
- `gpqa.jsonl` 选项重排：与 calib.jsonl 同步更新，**答案一致**
- `_provenance.json` 缺 mmlu 旧条目：mmlu.jsonl 本身未动（285 行）
