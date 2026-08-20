# CUA 评测平台计划

本文档是 CUA（Computer-Use Agent）评测平台的第一版方案：目标是在 Linux 上搭建一套可复现的实验基础设施，用来比较 **模型 + harness** 在多个公开 bench 上的表现。目标产出形态对齐 Qwen-CUA 论文 Table 1：按模型列、按 bench 行、支持单指标和双指标（binary / partial、task success / ASR）。

给后续编程 agent 的实现入口是仓库根目录 [AGENTS.md](../AGENTS.md)。本文第 7 节历史问卷已由 [REQUIREMENTS.md](./REQUIREMENTS.md) 关闭；剩下的是 **不阻塞阶段 0/1** 的延后项。

---

## 1. 要解决什么问题

评测对象不是「纯模型」，而是：

```text
Agent = Model + Harness + Action/Observation Protocol
```

同一模型换 harness（纯 GUI、GUI+Bash、OpenClaw skill、Agent-S3、厂商 native CUA API）分数会差很多。MacAgentBench 已证明：Claude Opus 在纯 GUI 约 39%，接到 OpenClaw skill library 后可到 73%。因此平台的一等公民必须是 **(model, harness, protocol, bench, version)**，而不是只记录模型名。

平台要做的事：

1. 用统一实验配置提交一次 run：指定模型、harness、bench、协议、步数上限、并行度。
2. 通过 adapter 调用各 bench **官方评测器**，不重写打分逻辑。
3. 把异构结果归一成同一 schema，生成 Table 1 风格总表，并保留轨迹、成本、失败原因。
4. 在 Linux 上可本地开发、可扩展到多机 / 云端并行。

平台**不做**的事（除非后续明确要求）：

- 不训练模型、不做 RL rollout 集群。
- 不替代官方 leaderboard 的「官方代跑」流程（OSWorld 官方 verified 榜需要作者侧复跑）。
- 不在 Linux 宿主机上假装跑出可发表的 macOS 分数。

---

## 2. 目标 bench 清单

| Bench | 环境 | 任务规模（公开数字） | 指标 | 官方栈 | Linux 可落地性 |
| --- | --- | --- | --- | --- | --- |
| OSWorld-Verified | Ubuntu 桌面 VM | ~360 | 成功率 % | Python + Docker/QEMU/AWS | 高，建议作为 MVP |
| OSWorld 2.0 | Ubuntu + 自托管 mock web | 108 长程任务 | binary / partial | Python，`xlang-ai/OSWorld-V2` | 高，但单任务极长（中位 ~1.6h 人类） |
| MyPCBench | Ubuntu 24.04 + 17 个已登录 web app | 184 | rubric / perfect-task；论文主表常用后者 | OSWorld-style Python runner | 高，需 gated qcow2 |
| MacAgentBench | 真实 macOS | 676 / 25 个 App | Pass@1 + checkpoint | 需 Mac 实例 | **Linux 无法原生跑** |
| Gym-Anything / CUA-World | Linux / Windows / Android | 200+ 软件，测试子集待确认 | checklist 成功率 | Python Gym API + Docker/QEMU | 中高，镜像与子集要选 |
| ScienceBoard | 科学软件桌面 VM | 169 | 成功率 % | Python + HF VM snapshot | 高，镜像大 |
| WebArena | 自托管网站集群 | 812 | 成功率 % | Python + Docker Compose | 高 |
| RedTeamCUA | OSWorld VM + WebArena/TAC web | 864 对抗样本 | task success / ASR | 混合沙箱 | 中，安全隔离要求高 |

双指标必须在 schema 里一等支持，不能压成一个 float：

- OSWorld 2.0：`binary` 与 `partial`
- RedTeamCUA：`task_success` 与 `asr`（ASR **越低越好**，总表和排序都要特殊处理）

参考对比列（论文 Table 1，不是平台内置模型）：Qwen-CUA、Qwen-3.7、GPT-5.5、Opus-4.8。平台应能接入任意 OpenAI 兼容 / Anthropic / 自托管 vLLM 端点，而不是写死这四个名字。

---

## 3. 语言与技术栈建议

### 3.1 结论（推荐）

| 层 | 推荐 | 理由 |
| --- | --- | --- |
| 平台核心、adapter、agent 协议 | **Python 3.11+** | 上述 bench 官方 runner 几乎全是 Python；硬换成 Go/Rust 只会多一层 FFI |
| 包与环境 | **uv + 锁文件** | 比 conda 更适合仓库级可复现安装 |
| 配置 | **Pydantic v2 + YAML** | 实验配置要校验：模型、协议、步数、随机种子 |
| CLI | **Typer** | `cua-eval doctor/run/report/prune` 一条命令可脚本化、可 CI |
| 控制面 API | 本阶段不做；预留进程内 Orchestrator 接口 | 确认仅 CLI。以后若加 HTTP，再挂 FastAPI |
| 任务队列（MVP 可不上） | 先进程内并行；规模上来用 **Redis + arq/Celery** 或 **K8s Job** | CUA 任务是小时级、有状态 VM，不适合短任务队列思维 |
| 元数据 | 本阶段 **只用本地 JSON 文件**，不上数据库；以后规模上来再评估 | 单机单题不需要 SQLite，已确认 |
| 轨迹与截图 | 本地 `results/`；保留天数可配 | 以后再接 MinIO/S3 |
| 桌面环境 | **Docker + QEMU/KVM**（沿用各 bench 官方 image） | 不要自研第二套 DesktopEnv |
| Web 环境 | **Docker Compose**（WebArena / mock sites） | 与官方部署对齐 |
| 前端 | **不做** | 已确认仅 CLI |
| 可观测 | 结构化 JSON log + **OpenTelemetry 可选** | 必须能回答：卡在 reset / model / eval 哪一步 |

明确不建议作为主语言：TypeScript 全栈、Java、纯 Bash。它们可以出现在前端或运维脚本里，但不该承载评测循环。

### 3.2 为什么是「包装官方 runner」，而不是「统一重写 env」

各 bench 的分数可信度来自官方 evaluator（文件状态、DOM、checkpoint、rubric、ASR 判定）。自研统一 Gym 接口再重写打分，几乎必然和论文数字对不上。

正确分层：

```text
实验配置 YAML
    → Orchestrator（调度、重试、预算、并发）
        → Benchmark Adapter（安装依赖、拉镜像、调官方脚本、解析原始结果）
            → 官方 DesktopEnv / WebEnv / Gym-Anything env
        → Agent Adapter（把统一 Observation/Action 接到具体 harness）
    → Result Store（归一化指标 + 原始产物）
    → Reporter（Table 1、成本、失败分类）
```

平台自有代码应尽量薄：**编排、协议适配、结果归一、资源治理**。环境真实性留给官方仓库，并用 **git pin / release tag / image digest** 锁版本。

### 3.3 Linux 开发机最低假设

- x86_64 Linux，Docker Engine，**KVM 可用**（`/dev/kvm`）。无 KVM 时 OSWorld 类 VM 会极慢，不适合作为主路径。
- 单机可做 smoke（1–5 题）；全量需要多机或云 VM 池。
- MacAgentBench 必须走远程 Mac（Mac mini / Anka / AWS EC2 Mac），或第一期标记为 `unsupported_on_linux`。

---

## 4. 架构

```text
                    ┌─────────────────────────────────────┐
                    │  experiment.yaml（仅 CLI 提交）     │
                    └─────────────────┬───────────────────┘
                                      │
                    ┌─────────────────▼───────────────────┐
                    │  Orchestrator（进程内，非 HTTP 服务）│
                    │  校验配置 · 配额 · 并发槽 · 重试    │
                    └───────┬───────────────────┬─────────┘
           ┌────────────────▼────┐     ┌────────▼─────────┐
           │ Agent Runtime       │     │ Env Runtime      │
           │ model client        │     │ docker / qemu    │
           │ harness loop        │     │ compose / remote │
           │ obs/action protocol │     │ mac worker       │
           └────────┬────────────┘     └────────┬─────────┘
                    │  screenshot, a11y?, bash? │
                    └────────────┬──────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │ Official Evaluator      │
                    │ (不改打分语义)           │
                    └────────────┬────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │ Result Store            │
                    │ trials · metrics · cost │
                    │ traces · screenshots    │
                    └────────────┬────────────┘
                                 │
                          Table 1 / CSV / API
```

### 4.1 三个核心抽象

**1. AgentSpec**

- `model`: `backend`（`dummy` / `openai_compat`）、`endpoint_kind`（`api` / `local`）、名称、温度、max tokens、thinking 开关
- `harness`: 本阶段枚举 `stub` / `deepseek_harness`；以后可扩 `openai_computer`、`anthropic_computer`、`openclaw`、`agent_s3`（标识符统一 snake_case）
- `protocol`: `observation` / `guest_actions` / `harness_bash` 三个独立开关，取值见 [REQUIREMENTS.md](./REQUIREMENTS.md) 第 6.3 节
- `limits`: `max_steps`（默认 50）/ `max_turns` / wall-clock timeout

Qwen-CUA 论文的主设定是 **只看截图、只键鼠**。平台必须能强制这个协议，也必须能跑「GUI+Bash」消融（MyPCBench 已有对照）。注意本项目的「禁 bash」指禁止绕过图形界面调软件 API，agent 在桌面 VM 里开终端打字仍属合法键鼠操作。

**2. BenchAdapter**

每个 bench 实现同一接口：

- `prepare()`：拉代码、镜像、assets（幂等）
- `list_tasks(split)`
- `run_trial(task_id, agent)` → 原始结果
- `normalize(raw)` → `MetricSet`
- `cleanup()`

Adapter 内部可以 `subprocess` 调官方 `run_multienv_*.py`，不必把对方代码 vendoring 进主仓库；用 git submodule 或独立 checkout + pin 即可。

**3. MetricSet**

```yaml
metrics:
  - name: success_rate
    value: 86.2
    higher_is_better: true
    unit: percent
  - name: binary_completion
    value: 18.5
    higher_is_better: true
  - name: partial_completion
    value: 48.4
    higher_is_better: true
  - name: asr
    value: 16.4
    higher_is_better: false
```

附加字段：完成任务数 / 总任务数、error 任务、token、步数、美元成本、wall time。Table 1 渲染器按 bench 选择主列格式（单值或 `a / b`）。

### 4.2 建议的仓库结构（实现阶段）

```text
cua-eval/
  docs/PLAN.md
  pyproject.toml
  configs/experiments/              # 一份 YAML = 一次可复现实验
  src/cua_eval/
    cli.py
    schema.py                       # AgentSpec, MetricSet, Run
    orchestrator/
    agents/                         # harness adapters
    benches/                        # one package per benchmark
    report/                         # table1, csv, html
  third_party/                      # pins, 不改官方评测语义
  tests/                            # schema + fake adapter，不依赖大镜像
```

---

## 5. 分阶段落地

阶段按「先能在 Linux 上跑通一条真实链路」切，而不是按论文表格从左到右铺满。

### 阶段 0 — 契约与骨架（当前文档之后的第一步实现）

- Pydantic schema、YAML 实验配置、CLI `run` / `report`
- Fake bench + fake agent：不启动 VM，用于 CI
- Table 1 渲染（含双指标、ASR 越低越好的标注）
- 结果目录约定：`results/<run_id>/<bench>/<model>/<task_id>/`

### 阶段 1 — MVP：OSWorld-Verified 单题

- Adapter 包装官方 OSWorld Docker provider，commit pin ≥ `091f5ef`
- Harness 用 **DeepSeek Harness**（screenshot → Qwen 小 VLM → click/type/scroll），自备 cordis.yml 去掉上游默认的 bash
- **只跑 1 题**（`5ea617a3-0e86-4ba6-aab2-dac9aa2e8d57`），`num_envs=1`，`max_steps=50`。小 split（例如 10 题）属于阶段 2
- 记录：分数、步数、token、失败类别（env reset / model / evaluator）

选它做 MVP 的原因：Linux 原生、文档成熟、和 Table 1 第一行对齐、社区 harness 最多。

### 阶段 2 — 再接 2 个 Linux 桌面 bench

优先顺序建议：

1. WebArena（环境是 Compose，比长程桌面便宜，能验证「非 OSWorld」adapter）
2. MyPCBench 或 ScienceBoard（真正的桌面 VM + gated image）

OSWorld 2.0 放在其后：任务太长，没有并发池会把迭代速度打死。

### 阶段 3 — 协议矩阵与多 harness

- 同一模型 × `{deepseek_harness, deepseek_harness+bash, 厂商 CUA API}`
- 强制 protocol 字段写入结果，避免把 GUI-only 和 GUI+Bash 写进同一列
- 成本与步数报表（论文 Figure 6 那类 efficiency，可后做）

### 阶段 4 — 难环境

- Gym-Anything：先锁测试子集，不要默认 10k 任务
- RedTeamCUA：独立网络命名空间、禁止出网打真实服务、ASR 与 task success 同时存
- MacAgentBench：远程 Mac worker；Linux 控制面只发 job、收结果
- OSWorld 2.0 全量：需要 VM 池和 mock site 部署

### 阶段 5 — 产品化（按需）

- Web 看板、对比两次 run 的 diff、轨迹回放
- 多租户配额、API key 保险库
- 与训练集群对接（非本项目 MVP）

---

## 6. 关键设计约束

1. **可复现**：pin bench commit、镜像 digest、任务 json、max_steps、分辨率、是否 headless。换一个 OSWorld 小版本分数就会漂。
2. **隔离**：一任务一环境；RedTeamCUA 默认无外网。评测机不要用开发者个人账号登录真实网站。
3. **失败不是 0 分**：reset 失败、VNC 挂掉、API 429 应记 `infra_error`，模型自身异常记 `model_error`，与 `task_fail` 分开，否则会污染 Table 1。失败类枚举以 [AGENTS.md](../AGENTS.md) 第 4 节为准：`ok` / `task_fail` / `infra_error` / `model_error`。
4. **并发模型**：瓶颈是 VM 和显示器，不是 Python。按 `num_envs` 和宿主机 RAM/KVM 槽位限流。
5. **官方数字 vs 内部数字**：默认定位是「内部可复现对比」。若要对齐论文/官方榜，必须逐 bench 核对协议（步数、是否 bash、是否 a11y）。Qwen-CUA 主文是 screenshot-only。
6. **成本**：全量 8 bench × 4 模型会是大量 API 与 VM 时间。必须支持 `task_ids` 过滤、断点续跑、按 token 预算熔断。计算 / 存储 / 网络分档见 [RESOURCES.md](./RESOURCES.md)。

---

## 7. 延后项（不阻塞阶段 0/1）

阶段 0/1 按仓库根目录 [AGENTS.md](../AGENTS.md) 直接实现。下列项等用到对应 bench 或租 GPU 机时再补：

- 各 bench 的安全语义与网络隔离（尤其 RedTeamCUA）
- 真实 Qwen+DeepSeek Harness+OSWorld 选哪类执行机（Windows PC / 云单机 / 集群）：用最小验证消耗决定，**不绑定 4090**
- 具体哪一个 Qwen 小模型 id 与端点地址（YAML 可配，两条 route 的形状已定）
- harness 侧 bash 是否永久禁止（本阶段一律关闭）
- Gym-Anything 测试子集、MyPCBench 主指标口径
- 官方 VM 镜像许可证与 gated 账号

已于 2026-08-20 关闭：模型接入路线、api/local 双路线、协议三开关、OSWorld 单题 task id 与 commit pin、存储形态、指标口径、`max_steps`。见 [REQUIREMENTS.md](./REQUIREMENTS.md) 第 6 节。

---

## 8. 已确认的默认值

详见 [REQUIREMENTS.md](./REQUIREMENTS.md)。摘要：

| 项 | 确认值 |
| --- | --- |
| 第一期范围 | Cursor VM 做阶段 0（fake+dummy）；真实推理验证 = OSWorld **1 题** + Qwen 小模型 + DeepSeek Harness |
| 协议 | 观测只给截图；桌面动作只给键鼠（guest 内开终端打字合法）；harness bash 关闭 |
| 用户界面 | 仅 CLI |
| 模型 | dummy 仅平台自测；真实验证走 OpenAI 兼容接口上的小 Qwen，`api` / `local` 两条 route |
| 计算后端 | 实现 `local_linux`；预留 `windows_pc` / `cloud_single` / `small_cluster` / `gpu_cluster` |
| 存储 | 本地目录，无数据库；`artifact_retention_days` 默认 14 |
| 指标 | 阶段 1 只记 OSWorld `success_rate`；逐题 0.0–1.0，聚合用百分数 |
| Bench 客户机 Mac/Windows | 不跑 |
| 成功标准 | 内部跑通与可复现对比，不对齐论文分数 |
| 并发与步数 | `num_envs=1`，`max_steps=50` |
| 出网 | 允许 |

其余延后项见第 7 节，不阻塞编码。

---

## 9. 下一步

按 [AGENTS.md](../AGENTS.md) 在 Cursor VM 实现阶段 0。真实 Qwen + DeepSeek Harness + OSWorld 1 题按最小资源探测后再选执行机。
