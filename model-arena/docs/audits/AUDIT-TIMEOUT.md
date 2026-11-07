# 单题失控与修复 · 2026-10-31

> B0 首跑在**第 25 题卡死 13 分钟**，追查发现是代码缺陷，不是参数问题。

## 现象

```
第 25 题 d2_logic_0001（5 人排队逻辑题）
记录停在 24 题，13 分钟无新增
但 llama-server CPU 从 9461s 涨到 15112s —— 一直在算
```

## 根因：`chat()` 从不发送 `max_tokens`

```python
def _payload(self, messages, stream, **kw):   # 通用
def chat(self, messages, retries=2, **kw):    # 非流式：完全没有 max_tokens
def chat_stream(self, messages, max_tokens=None, **kw):  # 只有流式有
```

llama.cpp 默认 `-n -1`（无上限）。一道让模型陷入循环推理的题，会一直生成到耗尽 131072 上下文，约 **1-2 小时**。

**所有 10 个 suite 都走 `chat()`**，所以这是全局缺陷。

## 为什么不能简单设 max_tokens=2048

实测已完成 24 题的输出长度：

| 统计 | 字符 | 约 tokens |
|---|---:|---:|
| 最小 | 1 | 1 |
| 中位 | 644 | 429 |
| **最大** | **16,689** | **11,126** |

最大那题（`pub_gpqa_0023`）跑了 132 秒，**大概率是答对的**。设 2048 会把它截断 —— 用户明确指出「max_tokens 可能影响回答质量」，这个担心是对的。

## 采用的方案：C + A 双保险

### A. `max_tokens=16384`（兜底）
覆盖实测最大值 11126，**不会截断任何合法回答**，只防 ∞ 循环。

### C. `deadline_s=300`（主防线）
**墙钟硬截止**，这才是关键——因为 `max_tokens` 只能防「输出太长」，防不了「单 token 生成极慢」。

```python
left = deadline_s - (time.perf_counter() - started)
if left <= 1:
    raise EndpointError("deadline %.0fs exceeded before attempt %d")
req_timeout = self.timeout if left is None else min(self.timeout, left)
```

注意：原来的 `urlopen(timeout=self.timeout)` **只管单次读超时**，对持续流式生成永远不触发。

## 超时记为 timeout，不是 error

遵循用户 2026-10-31 的判断：「超时即标注，不因为本地模型时长久就判 0 分」。

```python
is_timeout = "timed out" in msg.lower() or "deadline" in msg.lower()
rec["timed_out"] = True   # 与 error 分开
```

`valid_stats()` 仍把超时算进 n_errors（它无法判分，必须离开分母），
但新增 `timeout_count()` 单独暴露，报告里能区分「端点坏了」和「模型停不下来」。

## 验证（4 个用例全通过）

| 用例 | 结果 |
|---|---|
| max_tokens 默认值是否发出 | 服务端收到 16384 |
| 8s 慢请求 + deadline 2s | **2.0s 精确切断** |
| 0.3s 正常请求 + deadline 5s | 0.32s 正常返回，不受影响 |
| 显式 `max_tokens=None` | 服务端收到 None（真关闭） |

## 一个实现细节：`_UNSET` 哨兵

初版用 `if max_tokens is None: max_tokens = DEFAULT` —— 导致**显式传 None 想关闭也被覆盖**。
改用模块级 `_UNSET = object()` 区分「没传」与「传了 None」。

## 新增 CLI

```bash
python runner/run.py --suite canary --deadline-s 300 --max-tokens 16384
# 传 0 表示关闭该限制
```

## 影响的历史数据

**此前所有 D2 类评测如果遇到循环推理的题，结果都是「该题缺失」**（永远跑不完）。
从现在起会记成 `timed_out: true`，可被统计。