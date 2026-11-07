# 贡献指南

感谢你有兴趣改进 Model Arena。这个项目最需要帮助的地方不是代码,而是**题目**。

---

## 最有价值的贡献:新题目

公开基准(MMLU、GPQA)已经被刷到饱和,而**"模型在实际工作里到底好不好用"**
仍然缺少好的测量方法。所以最缺的是**贴合真实使用场景、又能自动判分**的题。

一条好题的标准:

| 要求 | 说明 |
|---|---|
| **真实** | 来自实际工作,不是为出题而编 |
| **可自动判分** | 规则化,不需要 LLM 裁判(否则每次评测都要花钱) |
| **有区分度** | 强模型和弱模型得分不同 |
| **答案客观** | 不依赖主观判断 |

**提交前请自问**:如果我用"最笨但合理的策略"回答这题,能拿多少分?
如果能拿满分,这题就没有区分度,应该改进。

### 题目格式

放到 `datasets/_templates/` 下,JSONL,每行一题:

```json
{
  "user": "题目原文",
  "tools": ["tool_a", "tool_b"],
  "should": ["tool_a"],
  "must_not": ["tool_b"],
  "reason": "为什么这样判"
}
```

或参考 `datasets/_samples/` 里的完整样例。

### 加新维度

如果你想加一整个维度(比如"多轮对话一致性"):

1. 在 `datasets/_templates/` 加模板
2. 在 `scripts/gen_datasets.py` 加生成函数
3. 在 `runner/` 加判分器(必须是规则化的)
4. 在 `runner/run.py` 的 `_judge_for` 里接线
5. **在 `runner/make_coarse.py` 里说明它对哪些参数敏感**

---

## 代码贡献

### 硬性要求

| 要求 | 原因 |
|---|---|
| **只用标准库** | 零依赖是核心承诺,不能为了一个函数引入 requirements.txt |
| **判分必须规则化** | 任何需要 LLM 裁判的判分都会让评测有边际成本 |
| **错误要可读** | 用户是陌生人,`FileNotFoundError` traceback 等于没提示 |
| **测量类改动要说明干扰** | 见下 |

### 关于测量代码

这个项目的核心承诺是"尽可能无干扰的测量,包括项目自身的干扰"。
任何影响测量的改动都要考虑:

- 新加的代码会不会和被测服务抢 CPU?
- 会不会在推理期间做重活(判分、写盘、网络)?
- 数字是否会被静默污染?

**反面教材**(本项目真实犯过):

```python
except EndpointError as e:
    rec = {"correct": False}     # ← API 出错被记成答错
```

这会让一次网络抖动看起来像模型变差。错误必须**单独标记并从分母剔除**。

### 关于判分器

判分器最容易犯的错是**只认一种表达形式**。真实案例:

| 模型答的 | 判分器以为 | 问题 |
|---|---|---|
| `6\sqrt{17}` | `6` | 不支持根式 |
| `\frac{10}{21}` | `10` | 不支持 LaTeX 分数 |
| `2023 年 12 月` | 不匹配 | 空格敏感 |
| 列出正确格子**并解释** | 多报了格子 | 解释被当成答案 |

**规律:模型答得越规范,越容易被误判。**

加判分逻辑时请用**真实模型的输出来测**,不要只用数据集里的标准格式。

---

## 提交前检查

```bash
# 语法
python -c "import ast,glob; [ast.parse(open(f,encoding='utf-8').read()) for f in glob.glob('runner/*.py')+glob.glob('scripts/*.py')+glob.glob('tools/*.py')]"

# 端到端(mock,不需要 GPU)
python runner/mock_server.py --port 8099 &
python runner/run.py --endpoint mock --suite smoke

# 首次运行体验(确认新用户能跑起来)
python runner/setup.py --check
```

---

## 不要提交的内容

见 `.gitignore`。要点:

- `results/` —— 你自己的评测结果
- `config/machine.json`、`config/models.json` —— 你的机器配置
- `cache/`、`datasets/public/` —— 可重新下载
- `reference/` —— 个人参考资料

---

## 报告问题

有用的报告包含:

1. 你在做什么(`python runner/xxx.py --参数`)
2. 期望发生什么
3. 实际发生了什么(完整输出)
4. `python runner/setup.py --check` 的结果
5. 你的环境(GPU、Python 版本)

**如果数字看起来不对**,请一并附上 `python runner/arena_cli.py env` 的输出 ——
环境干扰是很多"奇怪数字"的真实原因。
