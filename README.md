# Local AI Suite

本地 AI 工具集：在一台机器上既把模型**跑起来**，也把模型**量清楚**。

| 子项目 | 是什么 | 从哪开始 |
| --- | --- | --- |
| [`llama-rig/`](llama-rig/) | Windows 桌面端 LLM 推理控制台 — 一键切换 llama.cpp / KVMem / PrismML，显存守卫防爆显存，启动服务、配模型、看日志、直接聊天 | 到 [Releases](https://github.com/Ye-wtw/Local-AI-Suite/releases) 下载 `Llama-Rig-<version>.zip` 解压即用 |
| [`model-arena/`](model-arena/) | 多维度模型能力评测 — 同一套题、同一套判分器，比较本地模型与云端 API，并找出**最适合你这台机器**的启动参数 | 见 [`model-arena/README.md`](model-arena/README.md)，纯 Python 标准库、零第三方依赖 |

两个子项目互相独立：**装 Llama Rig 不需要 Python，跑 Model Arena 不需要 Electron。**

---

## 30 秒上手

### Llama Rig（桌面端推理控制台）

到 Releases 下载 `Llama-Rig-<version>.zip`，解压到任意有空间的目录，双击里面的 `Llama Rig.exe`。

- 数据和程序放在一起（不写 `%APPDATA%`、不写注册表），换机器把 `configs\` 拷过去即可；
- 未做代码签名，首次运行会看到 SmartScreen 提示，点「更多信息」→「仍要运行」；
- 从源码开始见 [`llama-rig/README.md`](llama-rig/README.md)。

### Model Arena（模型评测）

```bash
cd model-arena
python runner/setup.py            # 检查环境，自动生成配置；不需要 GPU 也能试用
python runner/run_all.py --endpoint local --preset-id my-preset
```

---

## 仓库结构

```
.github/workflows/release.yml   Llama Rig 的打包发布（push 标签 v* 触发）
llama-rig/                      Electron 桌面端（推理控制台）
model-arena/                    Python 评测系统（题库 + 判分器 + 参数扫描）
```

## 许可

MIT，见 [`LICENSE`](LICENSE)。两个子项目各自也带一份 LICENSE，含义相同。
