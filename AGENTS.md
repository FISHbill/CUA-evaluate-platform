# AGENTS.md — CUA 评测平台开发手册

给后续编程 agent 的实现说明书。需求以 [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md) 为准；架构背景见 [docs/PLAN.md](docs/PLAN.md)。**不要重新讨论技术栈或范围**，按本文开工。

语言：对用户用中文；代码、CLI、标识符用英文。

---

## 1. 目标

搭建 Linux 上的 CUA 评测平台：比较 **模型 + harness** 在公开 bench 上的表现。结果表形态对齐 Qwen-CUA Table 1（单指标，或 `binary / partial`、`task success / ASR`）。

当前交付：**阶段 0 必须在 Cursor VM 完成**（fake + dummy，无 GPU）。**阶段 1** 是真实 CUA 推理验证：OSWorld-Verified **1 题** + **任意 OpenAI 兼容端点上的视觉模型**（型号由 YAML 填写，不锁定；原候选 Qwen2.5 视觉系列可能改为 Qwen 3）+ **DeepSeek Harness**。

Cursor 开发 VM 已实测**不足以**跑阶段 1（4 vCPU / 15 GB、无 docker/qemu、`/dev/kvm` 普通用户不可读，低于 [docs/RESOURCES.md](docs/RESOURCES.md) 第 6.1 节的 8 vCPU / 32 GB）。因此在该 VM 上只实现 adapter 与 `doctor` 探测，真正跑题放到后续选定的执行机。

### 阶段 0 完成标准（必须，Cursor VM，不依赖 GPU/Docker 镜像）

- 可安装的 Python 包 + CLI
- fake bench + **dummy** 模型 + stub harness：`cua-eval run` 能跑完 1 题并写出结果目录
- `cua-eval report` 打出 Table 1 风格文本（含双指标列、ASR 标注越低越好）
- `ComputeBackend` 枚举存在，仅 `local_linux` 可运行；其余 raise `UnsupportedComputeBackend`
- `pytest` 覆盖 schema、CLI、假评测、失败分类；默认 CI **禁止**拉 VM 镜像

### 阶段 1 完成标准（真实推理验证）

- OSWorld-Verified **1 题**（`5ea617a3-0e86-4ba6-aab2-dac9aa2e8d57`），`num_envs=1`，`max_steps=50`
- Agent = **任意 OpenAI 兼容端点上的模型** + **DeepSeek Harness**；型号由 YAML `model.name` 填写，**不锁定**某一代号
- 模型接入是**厂商中立的 route 字典**：`api` / `local` 两类端点都要能接，加一个厂商只改配置
- 协议按 2.1 的四个开关。首轮：`observation=screenshot`、`guest_actions=mouse_keyboard`、`guest_shell=true`、`harness_shell=false`
- 环境失败记 `infra_error`，不得记成模型 0 分
- 无 KVM/镜像时：`cua-eval doctor` 说明缺什么并退出非 0，不要假装跑过

首轮是**截图 + 键鼠 + guest shell**：选定的模型是视觉模型（见 6.3），所以截图协议一开始就能用；bash 已确认允许，落点是桌面 VM 内部。`observation=text` 仍是 schema 支持的合法取值（给纯文本模型留的一列），但**不是**首轮配置。协议不同的 run 结果分列记录，不得混在同一列（见 2.1）。

---

## 2. 硬约束（违反即做错）

| 做 | 不要做 |
| --- | --- |
| Python 3.11+、uv、Pydantic v2、Typer、YAML | Web 看板、多用户、FastAPI 控制面 |
| 真实验证：`harness=deepseek_harness` + 多模态模型 | 把 dummy 的分数当成模型能力 |
| CI/骨架：dummy 模型 + stub harness | 在 Cursor VM 上强行下载 8 个 bench 镜像 |
| 模型经 OpenAI 兼容 HTTP；不绑定 4090 | 把 vLLM/厂商 SDK 写死在循环里 |
| 模型接入厂商中立：加厂商 = 加一条 route | 代码里出现厂商名分支、把某家模型当成唯一路径 |
| `observation=screenshot` 时模型必须声明 image modality，`doctor` 校验 | 拿纯文本模型跑截图协议 |
| `ComputeBackend`：实现 `local_linux`，预留 windows_pc / cloud_single / small_cluster / gpu_cluster | 本阶段实现 Windows 宿主机或 K8s 调度 |
| 官方 OSWorld evaluator；adapter 只包装 | 重写打分逻辑 |
| 协议四开关照实写进结果（见 2.1） | 给模型 a11y 树 / DOM / 软件 API；把不同协议的分数混进同一列 |
| bash 落在 **guest 内**（`guest_shell`） | 挂 `dsh-bash-local` 把宿主机 shell 给 agent |
| 模型接入同时支持 API 与本地两条 route | 只做一条路线、把端点写死在代码里 |
| Mac/Windows **bench 客户机** 标 `unsupported` | 在 Linux 上假跑 macOS 分数 |
| 本地 `results/` + `artifact_retention_days`（默认 14） | 本阶段上 MinIO/S3、上 SQLite 或任何数据库 |
| 密钥只从环境变量读 | API key 进 git / YAML / 桌面 VM |

非目标：训练、官方 leaderboard 代跑、复现论文绝对分数、本阶段跑满 8 个 bench。

### 2.1 协议定义（四个独立开关）

协议是一等公民：**每个开关都照实写进 `RunRecord.protocol`，取值不同的 run 不得进 Table 1 的同一列。**

| 开关 | 取值 | 含义 |
| --- | --- | --- |
| `observation` | `screenshot` \| `text` | `screenshot` 给截图；`text` 不给截图（纯文本模型只能走这个）。两者都**禁止** a11y 树、DOM、应用脚本接口等结构化读取 |
| `guest_actions` | `mouse_keyboard` \| `none` | 键鼠。**agent 在桌面 VM 里打开终端打字属于合法键鼠操作** |
| `guest_shell` | `true` \| `false`（**已开放**） | 桌面 VM **内部**的 shell，经 OSWorld 官方通道执行。这就是「允许 bash」该落的地方 |
| `harness_shell` | `false`（**保持关闭**） | dsh 宿主侧的 bash（`dsh-bash-local`），在跑 dsh 的那台机器上执行 |

#### 为什么 bash 必须落在 guest 侧，而不是 dsh 侧

这两个不是同一个东西，选错了实验就白做：

- `dsh-bash-local` 的包说明是「**Local** Service Provider」，`LocalBashExecutor` 用 `bash -c` 在 dsh 进程的 `cwd` 里起子进程——也就是**评测宿主机**，不是桌面 VM。
- OSWorld 的任务状态全在 guest 里（回收站里的文件、GIMP 的画布、LibreOffice 的文档）。宿主机上的 shell **改不动 guest 状态**，因此解不了题。
- 更糟的是它给了 agent 对评测设施本身的 shell：官方 evaluator 代码、任务 JSON、`results/` 目录、以及宿主机环境变量里的 API key 都在它手边。这是**打分完整性事故**，不是能力增强。

所以「允许 bash」的正确实现是 `guest_shell=true`：在本仓库的 MCP server 里加一个 shell 工具，底层调 OSWorld `PythonController.run_bash_script()`，命令在**桌面 VM 内**执行。`harness_shell` 保持 `false`，除非以后有明确理由要给 agent 宿主机权限——那属于另一个实验，且必须先解决完整性问题。

---

## 3. 仓库结构（按此创建）

```text
AGENTS.md
README.md
pyproject.toml
.github/workflows/ci.yml            # uv sync + pytest；禁止拉 VM 镜像
configs/experiments/smoke_fake.yaml
configs/experiments/smoke_osworld.yaml
configs/experiments/smoke_scienceboard.yaml
configs/experiments/smoke_mac_agent_bench.yaml
configs/dsh/osworld.cordis.yml   # 自备 dsh 组合，见 6.2
src/cua_eval/
  __init__.py
  cli.py                 # cua-eval
  errors.py              # UnsupportedComputeBackend, UnsupportedBenchError, ...
  schema.py              # Experiment, AgentSpec, Metric, RunRecord, Protocol, Limits
  backends/compute.py    # ComputeBackend: local_linux | windows_pc | cloud_single | small_cluster | gpu_cluster
  backends/model.py      # dummy | openai_compat（endpoint_kind: api | local）
  harness/base.py        # Harness protocol
  harness/stub.py        # CI 用，配合 dummy
  harness/deepseek.py    # DeepSeek Harness adapter（阶段 1）
  harness/desktop_mcp.py # 截图 + 键鼠的 MCP server，供 dsh 挂载，见 6.2
  orchestrator/run.py
  store/results.py       # 写 results/、按 retention 清理
  report/table1.py
  benches/base.py        # BenchAdapter protocol
  benches/fake.py
  benches/osworld.py     # 阶段 1；阶段 0 可先 stub
  benches/scienceboard.py
  benches/mac_agent_bench.py
  benches/lucwei_mac.py  # 远程 macOS guest（Fleet 截图 + SSH）
  benches/macos.py       # raise UnsupportedBenchError
  benches/windows.py     # raise UnsupportedBenchError
  actions.py             # 中立键鼠 schema → OSWorld pyautogui 映射
tests/
  test_schema.py
  test_cli_fake.py
  test_table1.py
  test_retention.py
  test_failure_classes.py   # ok / task_fail / infra_error / model_error 分类
  test_unsupported.py       # 未实现的 backend / bench 抛对应异常
docs/                    # 已有 PLAN / REQUIREMENTS / RESOURCES，勿删
third_party/OSWorld      # checkout，不进 git（见 .gitignore）
third_party/ScienceBoard
third_party/MacAgentBench
```

包名：`cua_eval`。入口：`cua-eval`。

---

## 4. 核心类型

评测对象主键：`(model, endpoint_kind, harness, protocol, bench, bench_version, compute_backend)`。

- 阶段 0：`harness=stub`，`model.backend=dummy`，`compute_backend=local_linux`
- 阶段 1：`harness=deepseek_harness`，`model.backend=openai_compat`，`bench=osworld_verified`

**模型接入不绑定任何厂商。** 评测对象是「任意 OpenAI 兼容端点上的模型」——具体型号由实验 YAML 的 `model.name` 填写，不锁定某一代号。DeepSeek V4 系列以及后续其他厂商的模型都必须能靠改配置接进来，不许在代码里出现厂商分支。

```text
model.backend           dummy | openai_compat
model.endpoint_kind     api | local          # openai_compat 时必填
model.provider_route    cordis.yml 里的 route 名（见 6.3）
model.name              模型 id
model.api_key_env       只写环境变量名
model.input_modalities  必须含 image（见下）
```

`endpoint_kind=api` 是云端模型 API（前期路线），`local` 是本地 vLLM 或计算卡集群网关（后续路线）。厂商与端点全部由 `provider_route` 指向的 cordis.yml route 决定，平台代码只认 route 名。`endpoint_kind` 必须写进 `RunRecord` 并在 Table 1 里可区分：同一个模型跑在云 API 还是本地卡上，分数可比但要能分辨。

**图像能力必须显式声明，否则模型看不到截图。** dsh 的两个 LLM adapter 都把未声明的模型当作纯文本（pi-ai 的 `DEFAULT_INPUT` 是 `['text']`，直连 adapter 是 `inputModalities ?? ['text']`），图像会在附加之前就被拒。因此：

- `protocol.observation=screenshot` 时，配置里必须给该 route 或该 model 声明 `image`（写法见 6.3），`doctor` 校验不过就直接失败，不要跑到一半才发现模型是瞎的。
- `protocol.observation=text` 时不要求 image 声明，但这条实验线**没有截图**，只能靠 `guest_shell` 观察与操作。它是一条合法的实验列，但**不是** CUA（GUI）能力的度量，不可与 Qwen-CUA Table 1 的 screenshot-only 数字并列比较。
- `doctor` 必须拒绝「纯文本 route + `observation=screenshot`」这种自相矛盾的组合。

`doctor` 对两类端点检查不同项——`api` 查密钥存在与端点联通，`local` 查端点存活；`screenshot` 协议下两类都要查 image 声明。

**运行限制**（`AgentSpec.limits`）：`max_steps` 默认 **50**，`num_envs` 默认 1，另有整题 wall-clock 超时。这些字段必须在 schema 里，`smoke_osworld.yaml` 要能覆盖。

**中立动作**（OSWorld 协议）：

```text
click {x, y, button?}
double_click {x, y}
move {x, y}
scroll {x, y, dx, dy}
type {text}
key {keys}          # e.g. ["ctrl", "s"]
wait {seconds}
terminate {status}  # success | fail
shell {command, timeout?}   # 仅当 protocol.guest_shell=true；在桌面 VM 内执行
```

`shell` 动作只有在 `guest_shell=true` 时才注册进工具集；为 `false` 时**必须完全不暴露**，不能只在执行时拒绝。它的落点是 guest，不是宿主机，理由见 2.1。

坐标：归一化到截图像素或 0–1 相对坐标，schema 里写死一种并在 OSWorld adapter 转换。推荐 **像素坐标**，与截图尺寸一起存。

**Metric**：`name`, `value: float`, `higher_is_better`, `unit?`。

数值口径（写死，不要再选）：

```text
逐题分数    OSWorld 官方 evaluator 原样返回的 0.0–1.0 float，存进 result.json
聚合指标    success_rate，百分数，unit="percent"，higher_is_better=true
```

即逐题存 `0.0 / 1.0`，汇总列显示 `50.0`（percent）而不是 `0.5`。本阶段**只记 OSWorld 这一个指标**；Table 1 渲染器仍须具备 `a / b` 双指标与 ASR 越低越好的能力（为 2.0 / RedTeamCUA 预留），但阶段 1 不启用。

**失败类**：`ok` | `task_fail` | `infra_error` | `model_error`。`infra_error` 不计入成功率分母时要在 report 里写清（例如 `2/2 scored, 1 infra skipped`）。

**结果目录**：

```text
results/<run_id>/<bench_id>/<model_id>/<task_id>/
  result.json          # 归一化分数与失败类
  raw/                 # 官方 runner 原始输出（阶段 1）
  screenshots/         # 逐步截图
  trace.jsonl          # 每步 observation 引用、动作、token
```

`run_id` 用时间戳+短随机，避免覆盖。`cua-eval prune` 按 `artifact_retention_days` 删过期 `results/<run_id>`。

**不引入 SQLite 或任何数据库**。本阶段只有单机单题，结果就是上面这些文件：`result.json` 供 `report` 解析，`table1.md` 供人读。

---

## 5. CLI

```text
cua-eval doctor              # 查 python/docker/kvm/磁盘/模型后端
cua-eval run -c <yaml>       # 跑实验
cua-eval report <run_id>     # Table 1 文本到 stdout，并写 results/<run_id>/table1.md
cua-eval prune               # 按配置清理过期产物
```

`smoke_fake.yaml`：`bench: fake`，`model.backend: dummy`，`harness: stub`，`compute_backend: local_linux`。

`smoke_osworld.yaml`：

```text
bench                     osworld_verified
bench_version             >= 091f5ef（见第 7 节；也接受 bench_commit 作别名）
task_ids                  [5ea617a3-0e86-4ba6-aab2-dac9aa2e8d57]
harness                   deepseek_harness
harness_version           pin 的 deepseek-harness-sdk 版本
cordis_config             configs/dsh/osworld.cordis.yml
limits.num_envs           1
limits.max_steps          50
limits.task_timeout_seconds       整题 wall-clock 上限
limits.max_screenshot_history     截图历史深度，按端点上下文容量调（见 6.4）
model.backend           openai_compat
model.endpoint_kind     api（前期）
model.provider_route    cordis.yml 里的 route 名
model.name              按实际 OpenAI 兼容端点填写，不锁定某一代号
model.api_key_env       环境变量名
model.input_modalities  [text, image]
protocol.observation     screenshot
protocol.guest_actions   mouse_keyboard
protocol.guest_shell     true
protocol.harness_shell   false
```

端点地址与密钥由使用方在环境搭好后提供，配置里只放 `api_key_env` 与 `baseURL` 的可改字段；在提供之前 `doctor` 应报「端点未配置」并非 0 退出，不要退化成 dummy。

换模型只改这份 YAML 与 cordis.yml 的 route，不改代码。若要跑纯文本模型，另存一份 `observation=text` 的 YAML 并列保存，不要互相覆盖。

无 GPU / 无端点时不要默认改成 dummy 还报「已验证模型」；应让 `doctor` 失败。

模型密钥：`api_key_env` 只写环境变量名。

---

## 6. 模型后端与 Harness

`ModelBackend.complete(...)`：

- `dummy`：平台自测，**不是**推理验证。
- `openai_compat`：接任意 OpenAI 兼容端点上的多模态模型——云端厂商 API、本地 vLLM、集群网关都是这一种。厂商与端点由 `provider_route` 决定，**这一层不得出现厂商分支**。不绑定 4090。

`Harness`：

- `stub`：配合 dummy，保证循环结束。
- `deepseek_harness`：包装 [deepseek-ai/deepseek-harness](https://github.com/deepseek-ai/deepseek-harness)（下称 dsh）。不要把 dsh 源码抄进本仓库。

`ComputeBackend.run_job`：本阶段 `local_linux` 即当前进程。`windows_pc` / `cloud_single` / `small_cluster` / `gpu_cluster` 仅 stub。

### 6.1 dsh 接入方式（已核实，不要按旧假设实现）

dsh 有**官方 Python SDK**，不需要 Node 工具链：

```text
包名 deepseek-harness-sdk（PyPI）  导入名 deepseek_harness
requires-python >=3.10   依赖 pydantic>=2.12,<3
装它会带 deepseek-harness-runtime-bin，内含独立 ELF 可执行 dsh-jsonrpc-agent
（linux-x64 / linux-arm64 / macos-arm64），SDK 用 JSON-RPC stdio 驱动它
```

adapter 直接用 `from deepseek_harness import DeepSeekHarness`，不要自己拼 Node CLI。

**版本 pin 与预发布陷阱**：SDK 目前只有预发布版（如 `0.1.1rc1`）。`uv` 若用 `--prerelease=allow`，pydantic 会被一起拉成 beta（实测 `2.14.0b1`），这会污染整个平台的 schema 层。pyproject 里必须写：

```toml
[tool.uv]
prerelease = "if-necessary"
```

只有确实没有正式版的包才走预发布，pydantic 仍解析到稳定版。当前锁定 `deepseek-harness-sdk 0.1.1rc1` 及其配套 runtime；不要用 `if-necessary-or-explicit`——uv 已把它标为弃用。dsh 版本号 pin 进实验 YAML。

### 6.2 自备 cordis.yml（阶段 1 的硬性前提）

`cordis.yml` 是 dsh 的插件组合清单。**禁止用 SDK 的零配置默认组合**：它挂了 `dsh-bash-local` / `dsh-subprocess-local` / `dsh-fs-local`，把**宿主机**的 shell 和文件系统交给模型——目标错了（改不动 guest 状态）且是打分完整性事故，理由见 2.1。

本仓库自备 `configs/dsh/osworld.cordis.yml`，纳入版本控制，与 dsh 版本一起 pin。相对默认组合必须做三件事：

1. **不挂** `dsh-bash-local`、`dsh-subprocess-local`、`dsh-fs-local`。**即使 `guest_shell=true` 也不挂**——guest 内的 shell 由本仓库 MCP server 的 `shell` 工具提供，不是由 dsh 的宿主 bash 提供。
2. **补挂** `dsh-attachment`。默认组合没有它，缺了截图进不了会话。
3. **挂** `dsh-llm-pi-ai` 声明模型 route（见 6.3），替换默认的 `dsh-llm-deepseek`。

上游**没有** computer-use 插件（已核实：仓库 9060 条路径中无任何 screenshot / 键鼠相关包），键鼠与截图工具必须自己提供。实现方式：本仓库写一个 Python MCP server 暴露第 4 节的中立动作，通过 dsh 的 `@deepseek-ai/dsh-mcp-client` 插件以 stdio 挂进 cordis.yml。这样键鼠代码留在本仓库，不必往 dsh 里写 TypeScript，且工具返回的图片是 dsh 官方支持的路径。

### 6.3 模型 route：厂商中立，一个插件挂任意多条

`dsh-llm-pi-ai` 是通用多 provider adapter，一个实例持有一个 route 字典；pi-ai 未内置的端点整份声明即可，OpenAI 兼容网关与自托管服务器都属于配置而非改代码。**加一个厂商 = 加一条 route，不动任何代码。** 实验 YAML 用 `model.provider_route` 选用哪条。

```yaml
- id: llm
  name: '@deepseek-ai/dsh-llm-pi-ai'
  config:
    providers:
      # 自建 route：任意 OpenAI 兼容云端点（endpoint_kind: api）
      vlm-cloud-a:
        api: openai-completions
        baseURL: <云端 OpenAI 兼容地址>
        apiKeyEnv: <环境变量名>
        defaultInput: [text, image]        # 关键：不写则整条 route 是纯文本
        models:
          - id: <模型 id>
            input: [text, image]           # 也可逐个模型声明
      # 自建 route：本地 vLLM 或集群网关（endpoint_kind: local）
      vlm-local:
        api: openai-completions
        baseURL: <本地 OpenAI 兼容地址>
        apiKeyEnv: <环境变量名>
        defaultInput: [text, image]
        models:
          - id: <模型 id>
      # 内置 catalog route：pi-ai 已收录的厂商，端点与模型清单都来自它
      deepseek:
        apiKeyEnv: <环境变量名>
        models:
          - id: <deepseek 视觉型号 id>
            input: [text, image]
```

要点：

- **`defaultInput` / `input` 不是可选项。** pi-ai 的默认值是 `['text']`，漏了这行整条 route 就是纯文本，截图在附加前即被拒。`defaultInput` 是 route 级兜底（一次声明覆盖该 route 全部模型），`models[].input` 是逐模型声明；两者都可用，至少要有一个含 `image`。
- `apiKeyEnv` 只写环境变量名，与第 2 节的密钥约束一致。
- 自建网关请求形状有差异时用 pi-ai 的 `compat` 字段修正（`thinkingFormat` / `supportsDeveloperRole` / `maxTokensField`），不要改 adapter 代码。
- 内置 catalog route 的 `models` 列表一旦声明就**替换**整个 catalog，只想改一个模型用 `modelOverrides`。
- 备选路径：`dsh-llm-deepseek` 直连 adapter 也支持 `inputModalities: [text, image]`，但它只注册一条固定 route（`deepseek-official`）且带 DeepSeek 专有的 thinking / `reasoning_content` 回传语义。**默认统一走 pi-ai**，只有需要 DeepSeek 官方 wire 语义时才挂它，且不要同时把同一厂商挂两遍。

#### 视觉 token 预算参考（以常见 7B VLM 为例）

下面的数字来自 Hugging Face 上一个 7B 视觉模型的公开配置，用来估算 1920×1080 截图的上下文占用。**这不是锁定的评测型号**——阶段 1 的 `model.name` 按实际 OpenAI 兼容端点填写。换型号后应按该模型的 patch size / 上下文长度重新核对，不要照搬。

| 项 | 参考值（7B VLM 一例） |
| --- | --- |
| 上下文 | `max_position_embeddings = 128000` |
| 视觉切块 | `patch_size = 14`，`spatial_merge_size = 2`，即 **28 px 一个视觉 token** |
| 预处理上限 | `min_pixels = 3136`，`max_pixels = 12845056`（约 3584×3584） |

由此得到的三条实现约束：

- 阶段 1 按视觉模型来配，所以直接用 `observation=screenshot`，route 上必须声明 `input: [text, image]`（或 route 级 `defaultInput`）。
- 1920×1080 远低于这类模型常见的 `max_pixels`，**不会**被预处理自动缩小，视觉 token 要按全尺寸算——见 6.4 的预算表。
- 端点地址与密钥待提供。是否真支持图像仍要靠接图连通性测试确认，**不要凭型号名假设**：声明正确但端点不支持图，只会在跑题中途才炸。

顺带一条通用警告：不要为了「能跑截图」给纯文本型号硬写 `input: [text, image]`。dsh 允许你这么声明，但请求会被端点以 400 拒绝，而图片此时已进入持久化历史，会把整个会话卡死在必然失败的重试上。dsh 出厂 catalog 里的 `deepseek-v4-flash` / `deepseek-v4-pro` 就都没有声明 `image`，属于纯文本型号。

### 6.4 截图格式与数量上限（dsh 会静默丢图）

dsh 对图像有两道上限，撞上了不会报错而是降级，必须按这些数字设计：

| 限制 | 默认值 | 后果 |
| --- | --- | --- |
| `maxImageDimension` | 2000 px/边 | 超限报 `IMAGE_DIMENSION_TOO_LARGE` 并毒化整个会话 |
| `maxRequestImageBytes` | 20 MiB 累计 base64 | 超限把最旧图片替换成占位符，**且不记 session event** |

因此定死：

- 送模型的截图用 **JPEG**，分辨率 **1920×1080**（1920 < 2000，恰好安全；不要提高分辨率）。PNG 单张 1–3 MB，base64 后约 5–15 张就触顶 20 MiB。
- 在 cordis.yml 里**显式**写出这两个上限，不要依赖默认值。
- 截图历史上限 20 张由**平台侧**裁剪并写进 `trace.jsonl`，记录每步实际送入的张数。不要依赖 dsh 的静默 offload——它不记事件，会让 trace 声称送了 20 张而模型只看到几张。

本阶段不必做 chunked folding。

#### 真正的瓶颈是模型上下文，不是 dsh 的 20 MiB

对上述 7B VLM 参考配置（28 px 一个视觉 token）估算 1920×1080 一张截图：

```text
smart_resize 到 28 的整数倍：1920→1932，1080→1092
视觉 token ≈ (1932/28) × (1092/28) = 69 × 39 ≈ 2,700 / 张
```

两道上限的先后顺序因此是：

| 约束 | 20 张 1920×1080 的用量 | 是否触顶 |
| --- | --- | --- |
| dsh `maxRequestImageBytes`（20 MiB base64） | JPEG 约 5–13 MB | 不触顶 |
| 模型上下文（128K） | 约 54K 视觉 token | 不触顶 |
| vLLM `--max-model-len`（**取决于显存**） | 同上 54K | **很可能触顶** |

结论与要求：

- **`--max-model-len` 必须显式设够**。7B 模型在单张 24 GB 卡上按默认参数常被压到 32K 左右，那样 20 张截图（约 54K）根本放不进去，会在跑题中途报上下文超限。走 `endpoint_kind=local` 时 `doctor` 要把端点实际的 `max_model_len` 读出来核对；容量不够就**下调截图历史深度**，别让它到跑题时才炸。
- 32K 可用上下文大约只能放 **8–10 张** 截图（还要留给文本与工具定义），所以历史深度必须是配置项而不是写死的 20。
- 如果为省显存下采样截图（例如 1280×720 约 1,200 token/张），**必须把模型返回的坐标按缩放比映射回原图**再执行——协议用的是像素坐标（见第 4 节）。漏了这步点击会系统性偏移，而且表现为「模型能力差」，极难排查。

---

## 7. Bench adapter

```python
class BenchAdapter(Protocol):
    id: str
    def prepare(self) -> None: ...
    def list_tasks(self) -> list[str]: ...
    def run_trial(self, task_id: str, agent) -> RawResult: ...
    def normalize(self, raw: RawResult) -> MetricSet: ...
    def cleanup(self) -> None: ...
```

- `fake`：内存任务，截图可用 64×64 纯色 PNG；evaluator 对 dummy 给固定分即可。
- `osworld_verified`：调用官方 Docker provider，**pin 官方仓库 commit**（写入配置，不要浮动 `main`）。不要把 OSWorld 源码复制进本仓库；checkout 到 `third_party/OSWorld`（已在 `.gitignore`）。
- `scienceboard`：包装官方 ScienceBoard `VMTask.eval()`（**不要**调用 `Tester()` 整段，那会跑他们自己的 agent）。环境盘是 VMware `.vmx` / `VM.zip`，adapter **禁止下载**。checkout 到 `third_party/ScienceBoard`。与 OSWorld 分列记录。
- `mac_agent_bench`：包装官方 MacAgentBench `MacOSEnv.evaluate_task()`。桌面落在远程 macOS guest（Fleet 截图 + SSH 键鼠），**不是**本机 Docker-OSX，也不是通用 `macos` bench。adapter **禁止下载 HDD**。checkout 到 `third_party/MacAgentBench`。与 OSWorld / ScienceBoard 分列记录。
- `macos` / `windows`：类存在（`benches/macos.py`、`benches/windows.py`），`prepare`/`run_trial` raise `UnsupportedBenchError`。通用 macOS 客户机仍不支持；MacAgentBench 是另一列。

**OSWorld commit pin 下限**：必须 ≥ `091f5ef1d5544bc74953c77875d5feb5bed30108`。该 commit（`fix(docker): remove orphaned anonymous volumes on container teardown`）修的正是每题泄漏约 32 GB 匿名卷直到写满磁盘的问题；pin 到它之前，[docs/RESOURCES.md](docs/RESOURCES.md) 第 6 节的磁盘估算不成立。

**阶段 1 的单题**（已确认，写进 `smoke_osworld.yaml`）：

```text
bench      osworld_verified
domain     os
task_id    5ea617a3-0e86-4ba6-aab2-dac9aa2e8d57
指令       从回收站恢复误删的 party night 海报
evaluator  exact_match
```

选它的理由：纯 GUI 文件管理器操作，官方 `test_small.json` 成员，setup 只有一次小下载。`os` 域另一题（`5812b315`，创建 SSH 用户）本质是终端命令任务，不适合用来验证截图+键鼠闭环。

---

## 8. 实现顺序（一次做完再停）

逐条的任务拆解、完成判据与验证方式见 [docs/EXECUTION_PLAN.md](docs/EXECUTION_PLAN.md)。下面是粗粒度顺序：

1. `pyproject.toml`（含 6.1 的 `[tool.uv] prerelease`）+ CLI help
2. `errors.py` + schema（compute_backend / harness 枚举、`protocol` 三开关、`limits`、`endpoint_kind`）与两份实验 YAML
3. dummy + stub + fake bench
4. `run` / `report` / `prune` / `doctor`
5. pytest（fake 路径 + 失败分类 + unsupported 分支）
6. `.github/workflows/ci.yml`
7. DeepSeek Harness adapter 骨架、`harness/desktop_mcp.py`、`configs/dsh/osworld.cordis.yml`、OSWorld adapter（无资源则 skip 真跑）

---

更新 [README.md](README.md)：安装、`uv sync`、两条 smoke 命令、`doctor`。保留 docs 链接。

---

## 9. 测试

- 无网、无 Docker 必须绿：`uv run pytest`
- 标记 `@pytest.mark.osworld` / `@pytest.mark.scienceboard` / `@pytest.mark.mac_agent_bench` 的测试默认 skip，除非对应的 `CUA_EVAL_OSWORLD=1` / `CUA_EVAL_SCIENCEBOARD=1` / `CUA_EVAL_MAC_AGENT_BENCH=1`
- 禁止在测试里下载 qcow2 / VM.zip / Mac HDD
- CI（`.github/workflows/ci.yml`）只跑 `uv sync` + `uv run pytest`：**不得**拉 VM 镜像、不得下载 qcow2、不得需要模型密钥

阶段 0 的 pytest 必须覆盖：schema 校验、CLI 假评测全链路、Table 1 渲染（含双指标与 ASR 方向）、retention 清理、四种失败分类、未实现 backend/bench 抛 `UnsupportedComputeBackend` / `UnsupportedBenchError`。

---

## 10. 推迟、不阻塞编码的项

实现时用合理默认，不要停下来问：

- 各 bench 安全语义
- 真实模型+dsh+OSWorld 跑在哪类执行机：用阶段 0/`doctor` 的最小消耗再选，不在代码里写死 4090
- 首轮用哪个厂商的哪个模型 id 与端点地址：`smoke_osworld.yaml` 里放可改字段，route 的形状已由 6.3 定好。**不要**因为这个未定就把某一家写进代码
- harness 侧 bash 是否永久禁止：本阶段一律 `false`，不要自行打开

已关闭、**不要**再当成开放问题的项（见 [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md) 第 6 节）：模型接入路线与厂商中立性、api/local 双路线、协议三开关、OSWorld 单题 id 与 commit pin、存储形态（无数据库）、指标口径、`max_steps=50`。上游 computer-use 插件**确认不存在**，按 6.2 自己写 MCP server，不要改成 coding bash 评测。

若与 REQUIREMENTS 冲突，以 REQUIREMENTS 为准并改本文。
