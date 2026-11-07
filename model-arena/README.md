# Model Arena

在**你自己的机器上**评测大模型：同一套题、同一套判分器，比较本地模型与云端 API，
并找出**最适合你这台机器**的启动参数。

**零第三方依赖** —— 只用 Python 标准库，不需要 pip install。

---

## 它回答什么问题

| 问题 | 对应功能 |
|---|---|
| 我本地这个模型到底什么水平？ | **12 个维度**的评测（1969 题 + D11 43 题 + D12 24 场景） |
| 换量化 / KV 精度 / 上下文长度后，能力掉了多少？ | 参数扫描 + 配对检验 |
| 哪个启动配置最适合我这台机器？ | 四阶段优化漏斗 |
| 本地模型和云端差在哪？ | 云端对比（配对 McNemar） |
| 这些分数可信吗？ | 干扰检测 + 置信区间 + **判分器版本** + 污染检测 |
| 模型会一本正经地编吗？ | 抗幻觉子类 + 反编造判据 |
| 上下文压缩后还准吗？ | D12 压缩降智（配对） |
| 模型真的看得见图吗？ | D11 视觉（代码生成图，精确答案） |
| 它会不会顺着用户做有害的事？ | 拒答 / 越权 / 注入 |

---

## 30 秒开始

```bash
git clone https://github.com/Ye-wtw/Local-AI-Suite.git
cd Local-AI-Suite/model-arena
python runner/setup.py            # 检查环境，自动生成配置
```

`setup.py` 会告诉你缺什么、下一步做什么。**不需要 GPU 也能试用**：

```bash
python runner/mock_server.py --port 8099 &     # 假模型
python runner/run.py --endpoint mock --suite smoke
```

---

## 四个最常用的命令

```bash
# 1. 一键跑完所有维度，跑完自动出报告与榜单（推荐从这里开始）
python runner/run_all.py --endpoint local --preset-id my-preset

# 2. 只想快速看方向（137 题，约 25 分钟）
python runner/run.py --endpoint local --suite canary --tag my-test

# 3. 看所有模型排名
python runner/build_report.py

# 4. 两个模型配对比较（谁在哪题赢）
python runner/arena_cli.py compare --a local --b deepseek-flash --level canary
```

`run_all.py` 还支持 `--only D2,D3`（只跑指定维度）、`--skip D7`（跳过某维度）、
`--limit 2`（每维度只跑两题做冒烟）、`--dry`（只打印计划不真跑）。
它会打印一张汇总表，**哪个维度整维跳过、为什么跳过都会写明** ——
D11 需要引擎加载 mmproj，没有视觉通道的端点会被跳过而不是记 0 分。

---

## 四档题量（先小后大，别一上来跑全量）

| 级别 | 题量 | 耗时 | 用途 |
|---|---:|---|---|
| `coarse` | 20 | ~1 分钟 | **参数扫描专用**（高敏感度题） |
| `smoke` | 24 | ~5 分钟 | 验证配置通不通 |
| `canary` | 137 | ~25 分钟 | **主力档**，够做配对显著性检验 |
| `full` | 1969 | 5–10 小时 | 只在需要最终定论时跑 |

---

## 动手改判分器之前，先读这个

**判分器比模型更容易出错。** 一次全量跑测（1949 题 × 2 端点）抓出 7 个缺陷，
**6 个在判分/构建侧**，只有 1 个是真模型弱点。这些都是实际发生过的：

| 缺陷 | 症状 | 教训 |
|---|---|---|
| 「回答长度 > 250 = 编造」 | 352 字全对的答案被判错 | 长度不是编造的证据 |
| `\b` 在中文旁失效 | `amount\w*` 吞掉整段混排文本 | CJK 边界要用首尾字规则 |
| 「无法**可靠**核实」匹配不到「无法核实」 | 明确拒绝的答案被判编造 | 中文会在词组中间插副词 |
| SQL 参考解 JOIN 错列 | 云端答对被判 0/3 | 写 SQL 题必须核对列类型 |
| exec 题面不含文件 | 44 题模型回「请粘贴文件」 | 材料在 `exec.files`，要拼进 prompt |
| 判分分支查错对象 | 44 题从未真正执行 | exec 块在题库行 `q`，不在 baseline `b` |
| 拒答判分被「不能撕破脸」绕开 | 给了施压信模板却判通过 | 判**是否交付成品**，不判开头有没有拒绝词 |

完整清单见 `AGENTS.md` 第 4 条。

---

## 判分器版本：为什么你的旧分数不能信

判分器一改，**之前所有分数就失去意义**。本项目用源码哈希做版本号：

```bash
python runner/judge_version.py     # 当前版本，如 6696c10c96fa
```

- 保存结果时自动打标
- `build_report.py` 检测版本不符，**打印警告而不是静默混用**
- 改完判分器后用 `rejudge.py` 重算历史数据（不重跑模型，零成本）：

```bash
python runner/rejudge.py --latest --level full --write
```

---

## 题库自检（秒级，不调模型）

```bash
python runner/selfcheck.py           # 报告
python runner/selfcheck.py --strict  # 有 HIGH 就 exit 1（可挂 CI）
```

能发现：空题面、无判据、注入题缺正文、重复 id、exec 命令无解释器、
subsets 与题库不一致。**跑全量前先跑这个。**

---

## 目录

```
doctor.py            环境体检（新到手先跑这个）
start-1..5*.bat      双击入口：自检 / 评测 / 测速 / 出榜单 / 开界面
ui.html              前端（单文件，零依赖）
runner/              判分器与执行器（89 模块）+ arena_api.py（后端 API）
scripts/             题库生成器与工具
datasets/            full/ 正式题库, public/ 权威基准, fixtures/ 测试夹具
results/             结果（gitignored）
config/              端点与机器配置（machine.json 已 gitignored）
docs/                USAGE / ARCHITECTURE / CONTRIBUTING / STATUS + dev/ audit/ plans/
reference/           参考资料
```

**拿到项目第一件事**：`python doctor.py` —— 它会告诉你缺 Python 版本、
缺题库、还是缺本机配置，而不是让你在跑测时报一句看不懂的路径错误。

---

## 读数时最容易被骗的三处

**① 加权总分 ≠ 能力。** 权重是自设计的，换一组排名可能翻转。**优先看逐维度**。

**② 准确率差异可能只是噪声。** 2pp 差距在小样本上毫无意义。
判读要配 McNemar p 值或 95% 置信区间（`runner/stats.py`）。

**③ 覆盖度不足的模型不该排第一。** 只跑了「数学」一个维度也可能是 100%。
覆盖度低于 60% 的结果不能下结论。

---

## 已知局限

- **判分器版本只覆盖判分文件**。改题库不触发版本变化，但旧分数同样失效——
  这种情况当前机制检测不到，需人工判断。
- **D7 长上下文**需要引擎 ctx ≥ 题目要求，否则该题被跳过。跨端点比较前必须对齐 ctx 上限。
- 云端跑全量有真实成本（deepseek-flash 约 14 元 / 1949 题）。
- 视觉（D11）需要引擎加载 mmproj，显存不够时会挤掉 KV cache。

---

## License

MIT
