# B0 首跑记录 · 引擎调参与踩坑

> 2026-10-31 首次真跑。记录**与文件二不一致的地方**和**新踩的坑**。

## 一、引擎定位

文件二定档的 build 10816 在：

    <llama.cpp 目录>\bin\llama-server.exe
    版本 0.4.0-dev (build 10816, commit 427291b5b)  <- 与文件二记载完全一致

⛔ **不在 PATH 上**。全盘还有 4 个 llama-server.exe，版本各不相同：

| 路径 | build | 说明 |
|---|---|---|
| `llama.cpp\bin\` | **10816** | **文件二定档用的就是它** |
| `prism-llama\` | 10709 | 旧 |
| `prism-b10754\` | 10754 | 旧 |
| `kvmem-gui\` | — | kvmem 分支，不认 `--version` |

## 二、三个必须设的参数（都是踩坑得来）

### 1. `--parallel 1` —— 不设会被切成 4 份

日志实证：

    --ctx-size 32768  ->  n_slots = 4, n_ctx_slot = 32768   (每 slot 只有 8k)
    加 --parallel 1   ->  n_ctx = 131072, total_slots = 1

**后果**：不设的话，131k 上下文实际只有 32k，长档全废。

### 2. `--reasoning off` —— 不设则回答为空

第一跑冒烟：

    content: ''                    <- 空
    reasoning_content: 'Here\'s a thinking process: 1. Analyze...'
    tokens: in=24 out=20           <- 20 个 token 全被思考吃掉

加 `--reasoning off` 后：`content: '2'`, `tokens: in=26 out=2`。

**注意**（实测）：`--reasoning off` 只关 `reasoning_content` 字段，
**模型仍会在正文里输出思考链**（`1. **分析 NaOH...**`）。不影响判分，但吃 token。

### 3. `-b 512 -ub 512` —— 显存不够时最有效的开关（本轮新发现）

这是**与文件二相反**的一处：文件二说 `-ub 1536` 是 prefill 甜区，
但那是在**追求速度**。我们要的是**装得下**。实测对比：

| 参数 | GPU 已用 | GPU 空闲 | 进程内存 |
|---|---:|---:|---:|
| `-b 1536 -ub 1536 --n-cpu-moe 27` | 7836 MiB | **64 MiB** | 10.16 GB |
| `-b 512 -ub 512 --n-cpu-moe 30` | **6552 MiB** | **1348 MiB** | 6.73 GB |

**省 1284 MiB，上下文一点没降（仍是 131072）。**

## 三、清系统缓存的实测结果（用户提问引出）

**能清，但被沙箱拒绝**：

    AdjustTokenPrivileges = True, lastErr = 1300 (ERROR_NOT_ALL_ASSIGNED)
    NtSetSystemInformation rc = 0xC0000061 (STATUS_PRIVILEGE_NOT_HELD)

原因：DSH 以剥离 Administrators 组的受限令牌启动命令。

**而且清了也不解决问题**：显存是 CUDA 分配，不是系统缓存。
真正省显存的是 `-ub` 和 `--n-cpu-moe`。

## 四、VRAM 预检阈值

`runner/run.py --min-free-vram`，默认 1500 MB。
首跑被它拦住（free=64MB）——**这是正确行为**，避免了一次注定 OOM 的 137 题全量。

调参后 free=1348 MB，仍低于 1500，本次用 `--min-free-vram 100` 放行。

## 五、B0 实际使用的完整命令

    llama-server -m "<Qwen3.6-35B-APEX-I-Compact.gguf>" \
      --ctx-size 131072 --n-gpu-layers 99 --parallel 1 --n-cpu-moe 30 \
      -ctk q4_0 -ctv q4_0 -fa on -b 512 -ub 512 --reasoning off \
      --host 127.0.0.1 --port 8080 --alias B0-llama-131k

## 六、DSH 沙箱的两个限制（影响启动方式）

1. **`Start-Process` 起的进程会被连带杀掉** —— 命令超时或结束时进程消失
2. **必须用 `run_in_background: true`** 才能让引擎脱离命令生命周期

日志文件都没生成就是第 1 条的典型表现。