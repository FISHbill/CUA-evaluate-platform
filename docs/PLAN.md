# CUA 评测平台计划

本文档是 CUA（Computer-Use Agent）评测平台的第一版方案：目标是在 Linux 上搭建一套可复现的实验基础设施，用来比较 **模型 + harness** 在多个公开 bench 上的表现。目标产出形态对齐 Qwen-CUA 论文 Table 1：按模型列、按 bench 行、支持单指标和双指标（binary / partial、task success / ASR）。

当前仓库为空，本文件只定方向与边界，不开始实现。实现前必须先确认文末「待确认需求」。

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
| CLI | **Typer** | `cua-eval run/status/report` 一条命令可脚本化、可 CI |
| 控制面 API | **FastAPI** | 提交 job、查进度、拉 Table 1；本地方便，以后也好接前端 |
| 任务队列（MVP 可不上） | 先进程内并行；规模上来用 **Redis + arq/Celery** 或 **K8s Job** | CUA 任务是小时级、有状态 VM，不适合短任务队列思维 |
| 元数据 | **PostgreSQL**（开发可用 SQLite） | run / trial / metric / artifact 需要查询和去重 |
| 轨迹与截图 | **对象存储（MinIO / S3）** | 单条 OSWorld 2.0 轨迹截图可达 GB 级 |
| 桌面环境 | **Docker + QEMU/KVM**（沿用各 bench 官方 image） | 不要自研第二套 DesktopEnv |
| Web 环境 | **Docker Compose**（WebArena / mock sites） | 与官方部署对齐 |
| 前端（可后置） | MVP 用 CLI + Markdown/HTML 报表；需要 UI 时再 **React + Vite** | 先把分数跑通，再做看板 |
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
                    │  experiment.yaml  /  REST 提交      │
                    └─────────────────┬───────────────────┘
                                      │
                    ┌─────────────────▼───────────────────┐
                    │  Control Plane                      │
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

- `model`: 名称、endpoint、温度、max tokens、thinking 开关
- `harness`: 例如 `native-cua`、`native-cua+bash`、`openai-computer`、`anthropic-computer`、`openclaw`、`agent-s3`
- `protocol`: observation（默认 screenshot-only）、action space、是否允许 DOM/a11y/shell
- `limits`: `max_steps` / `max_turns` / wall-clock timeout

Qwen-CUA 论文的主设定是 **只看截图、只键鼠**。平台必须能强制这个协议，也必须能跑「GUI+Bash」消融（MyPCBench 已有对照）。

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

### 阶段 1 — MVP：OSWorld-Verified 子集

- Adapter 包装官方 OSWorld Docker provider
- 一个 native CUA harness（screenshot → 模型 → click/type/scroll）
- 先跑 1 个 smoke 任务，再跑一个小 split（例如 10 题）
- 记录：分数、步数、token、失败类别（env reset / model / evaluator）

选它做 MVP 的原因：Linux 原生、文档成熟、和 Table 1 第一行对齐、社区 harness 最多。

### 阶段 2 — 再接 2 个 Linux 桌面 bench

优先顺序建议：

1. WebArena（环境是 Compose，比长程桌面便宜，能验证「非 OSWorld」adapter）
2. MyPCBench 或 ScienceBoard（真正的桌面 VM + gated image）

OSWorld 2.0 放在其后：任务太长，没有并发池会把迭代速度打死。

### 阶段 3 — 协议矩阵与多 harness

- 同一模型 × `{native-cua, native-cua+bash, 厂商 CUA API}`
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
3. **失败不是 0 分**：reset 失败、VNC 挂掉、API 429 应记 `invalid` / `infra_error`，与模型失败分开，否则会污染 Table 1。
4. **并发模型**：瓶颈是 VM 和显示器，不是 Python。按 `num_envs` 和宿主机 RAM/KVM 槽位限流。
5. **官方数字 vs 内部数字**：默认定位是「内部可复现对比」。若要对齐论文/官方榜，必须逐 bench 核对协议（步数、是否 bash、是否 a11y）。Qwen-CUA 主文是 screenshot-only。
6. **成本**：全量 8 bench × 4 模型会是大量 API 与 VM 时间。必须支持 `task_ids` 过滤、断点续跑、按 token 预算熔断。

---

## 7. 待确认需求

下面各项会改变技术选型和第一期范围。没有这些答案时，实现应停在阶段 0 骨架，或只做 OSWorld smoke。

### 7.1 范围与成功标准

1. 第一期必须跑通哪几个 bench？是否接受「先 OSWorld-Verified smoke，其余只留 adapter 接口」？
2. 成功标准是「能出内部对比表」，还是「必须复现 Qwen-CUA Table 1 量级数字」？后者对协议、镜像、max_steps 的对齐要求高一个数量级。
3. 是否需要对外 leaderboard / 多用户 Web，还是团队内部 CLI 即可？

### 7.2 模型与 harness

4. 首批要接哪些模型端点（DashScope / OpenAI / Anthropic / 自托管 vLLM）？密钥如何注入？
5. 第一期 harness 是否只做 **native screenshot + 键鼠**（对齐 Qwen-CUA 主设定），还是必须同时支持厂商 Computer Use API 和 OpenClaw？
6. 是否允许 bash / 文件工具？若允许，Table 1 是否拆成两列，避免和 GUI-only 混比？
7. 自研 harness 的动作空间是否直接复用 OSWorld `pyautogui` 风格，还是要做一层中立 schema 再映射？

### 7.3 基础设施

8. 运行形态：单台 Linux 开发机、内网 GPU/CPU 集群，还是公有云 VM 池？有无 KVM？
9. 全量并行目标大概多少并发环境（1 / 8 / 64）？这决定要不要上 K8s。
10. MacAgentBench：是否有 Mac mini / 云 Mac？没有的话第一期是否直接标记不支持？
11. Gym-Anything 是否包含 Windows / Android，还是只跑 Linux 子集？
12. 镜像与 gated assets（OSWorld-V2、MyPCBench qcow2、ScienceBoard snapshot）的下载账号与磁盘预算？

### 7.4 评测语义

13. 每个 bench 的 split、`max_steps`、分辨率、headless、随机种子，是否强制与某篇论文 appendix 对齐？请指定论文/commit。
14. OSWorld 2.0 全量还是先用官方小子集？单任务可达数百步、数小时。
15. RedTeamCUA 的 ASR 展示规则：总表是否始终 `success / ASR`，排序时 ASR 是否单独成「安全维度」而不是和能力分加权？
16. MyPCBench 主指标用 perfect-task rate 还是 rubric score？（论文主表与附录口径可能不同。）

### 7.5 数据、安全、合规

17. 轨迹、截图、模型 completion 要存多久？能否含用户 persona 数据（MyPCBench）？
18. 评测环境能否访问外网？WebArena / mock 站点是自托管还是用作者公共后缀？
19. RedTeamCUA 是否在隔离集群跑，产物是否需脱敏后才能进共享盘？
20. 许可证：部分 bench 与 VM 镜像是 gated / 非商用，平台默认开源还是内部仓库？

### 7.6 工程约束

21. 目标用户是研究同学手工跑，还是要进 CI（每次模型发布自动抽测 N 题）？
22. 语言约束：除 Python 核心外，前端/运维是否有团队偏好（React 或纯 CLI）？
23. 是否需要与现有实验平台（内部 job 系统、wandb、mlflow）对接？
24. 预算上限：单次全量 run 的 API + 机器费用能否接受，熔断策略是什么？

---

## 8. 建议的默认假设（若短期无法逐条确认）

在你回复前，实现侧若必须开工，将采用这些可撤销默认值：

| 项 | 默认 |
| --- | --- |
| 第一期范围 | 阶段 0 骨架 + OSWorld-Verified smoke（1–10 题） |
| 协议 | screenshot-only native CUA；bash 作为显式实验 flag |
| 用户界面 | CLI + Markdown Table 1 |
| 存储 | 本地目录；schema 预留 S3 |
| Mac / Windows | 接口预留，第一期不跑 |
| 成功标准 | 内部可复现，不承诺 bit-exact 复现论文表 |
| 并发 | 单机 `num_envs=1`，配置里可加大 |

---

## 9. 下一步

1. 你确认第 7 节中的范围、模型端点、是否要 Mac、成功标准。
2. 按阶段 0 落地 schema / CLI / fake adapter / Table 1 渲染。
3. 再接 OSWorld-Verified Docker smoke，用真实官方 evaluator 打通一条链路。
