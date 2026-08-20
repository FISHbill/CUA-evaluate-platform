# AGENTS.md — CUA 评测平台开发手册

给后续编程 agent 的实现说明书。需求以 [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md) 为准；架构背景见 [docs/PLAN.md](docs/PLAN.md)。**不要重新讨论技术栈或范围**，按本文开工。

语言：对用户用中文；代码、CLI、标识符用英文。

---

## 1. 目标

搭建 Linux 上的 CUA 评测平台：比较 **模型 + harness** 在公开 bench 上的表现。结果表形态对齐 Qwen-CUA Table 1（单指标，或 `binary / partial`、`task success / ASR`）。

当前交付：**阶段 0 必须在 Cursor VM 完成**（fake + dummy，无 GPU）。**阶段 1** 是真实 CUA 推理验证：OSWorld-Verified **1 题** + **Qwen 小模型** + **DeepSeek Harness**。

Cursor 开发 VM 已实测**不足以**跑阶段 1（4 vCPU / 15 GB、无 docker/qemu、`/dev/kvm` 普通用户不可读，低于 [docs/RESOURCES.md](docs/RESOURCES.md) 第 6.1 节的 8 vCPU / 32 GB）。因此在该 VM 上只实现 adapter 与 `doctor` 探测，真正跑题放到后续选定的执行机。

### 阶段 0 完成标准（必须，Cursor VM，不依赖 GPU/Docker 镜像）

- 可安装的 Python 包 + CLI
- fake bench + **dummy** 模型 + stub harness：`cua-eval run` 能跑完 1 题并写出结果目录
- `cua-eval report` 打出 Table 1 风格文本（含双指标列、ASR 标注越低越好）
- `ComputeBackend` 枚举存在，仅 `local_linux` 可运行；其余 raise `UnsupportedComputeBackend`
- `pytest` 覆盖 schema、CLI、假评测、失败分类；默认 CI **禁止**拉 VM 镜像

### 阶段 1 完成标准（真实推理验证）

- OSWorld-Verified **1 题**（`5ea617a3-0e86-4ba6-aab2-dac9aa2e8d57`），`num_envs=1`，`max_steps=50`
- Agent = **Qwen 小尺寸 VLM**（OpenAI 兼容 `base_url`）+ **DeepSeek Harness**
- 模型接入实现 **api / local 两条 route**，前期只填云 API 那条
- 协议按 2.1 的三个开关：只给截图、只给键鼠、dsh 侧 bash 关闭
- 环境失败记 `infra_error`，不得记成模型 0 分
- 无 KVM/镜像/GPU 时：`cua-eval doctor` 说明缺什么并退出非 0，不要假装跑过

---

## 2. 硬约束（违反即做错）

| 做 | 不要做 |
| --- | --- |
| Python 3.11+、uv、Pydantic v2、Typer、YAML | Web 看板、多用户、FastAPI 控制面 |
| 真实验证：`harness=deepseek_harness` + Qwen 小模型 | 把 dummy 的分数当成模型能力 |
| CI/骨架：dummy 模型 + stub harness | 在 Cursor VM 上强行下载 8 个 bench 镜像 |
| 模型经 OpenAI 兼容 HTTP；不绑定 4090 | 把 vLLM/厂商 SDK 写死在循环里 |
| `ComputeBackend`：实现 `local_linux`，预留 windows_pc / cloud_single / small_cluster / gpu_cluster | 本阶段实现 Windows 宿主机或 K8s 调度 |
| 官方 OSWorld evaluator；adapter 只包装 | 重写打分逻辑 |
| OSWorld 协议：截图 + 键鼠（见 2.1） | 给模型 a11y 树 / DOM / 软件 API |
| 模型接入同时支持 API 与本地两条 route | 只做一条路线、把端点写死在代码里 |
| Mac/Windows **bench 客户机** 标 `unsupported` | 在 Linux 上假跑 macOS 分数 |
| 本地 `results/` + `artifact_retention_days`（默认 14） | 本阶段上 MinIO/S3、上 SQLite 或任何数据库 |
| 密钥只从环境变量读 | API key 进 git / YAML / 桌面 VM |

非目标：训练、官方 leaderboard 代跑、复现论文绝对分数、本阶段跑满 8 个 bench。

### 2.1 协议定义（三个独立开关）

「禁止 bash」指的是**禁止模型绕过图形界面、通过软件的 API / 脚本接口直接完成操作**，不是禁止模型打字。三个开关分开配、分开记：

| 开关 | 本阶段取值 | 含义 |
| --- | --- | --- |
| `observation` | `screenshot`（锁定） | 只给截图。**禁止** a11y 树、DOM、应用脚本接口等任何结构化读取 |
| `guest_actions` | `mouse_keyboard`（锁定） | 只有键鼠。**agent 在桌面 VM 里打开终端打字是合法键鼠操作，不受限制** |
| `harness_bash` | `false`（默认关，取值待定） | dsh 侧的 bash 工具，即在宿主/工作区直接执行命令 |

`harness_bash` 以后若打开，**必须**作为独立实验列，`protocol` 字段照实写进 `RunRecord`。GUI-only 与 GUI+Bash 的分数不得进同一列。

---

## 3. 仓库结构（按此创建）

```text
AGENTS.md
README.md
pyproject.toml
.github/workflows/ci.yml            # uv sync + pytest；禁止拉 VM 镜像
configs/experiments/smoke_fake.yaml
configs/experiments/smoke_osworld.yaml
configs/dsh/osworld_gui_only.cordis.yml   # 自备 dsh 组合，见 6.2
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
```

包名：`cua_eval`。入口：`cua-eval`。

---

## 4. 核心类型

评测对象主键：`(model, endpoint_kind, harness, protocol, bench, bench_version, compute_backend)`。

- 阶段 0：`harness=stub`，`model.backend=dummy`，`compute_backend=local_linux`
- 阶段 1：`harness=deepseek_harness`，`model.backend=openai_compat`（Qwen 小 VLM），`bench=osworld_verified`

**模型接入两条路线**（都是 OpenAI 兼容 HTTP，区别只在端点在谁家）：

```text
model.backend        dummy | openai_compat
model.endpoint_kind  api | local        # openai_compat 时必填
model.name           模型 id
model.api_key_env    只写环境变量名
```

`endpoint_kind=api` 是云端模型 API（前期路线），`local` 是本地 vLLM 或计算卡集群网关（后续路线）。两者映射到 6.3 里 cordis.yml 的两条 route。`endpoint_kind` 必须写进 `RunRecord` 并在 Table 1 里可区分：同一个模型跑在云 API 还是本地卡上，分数可比但要能分辨。`doctor` 对两条路线检查不同项——`api` 查密钥存在与端点联通，`local` 查端点存活。

**运行限制**（`AgentSpec.limits`）：`max_steps` 默认 **50**，`num_envs` 默认 1，另有整题 wall-clock 超时。这些字段必须在 schema 里，`smoke_osworld.yaml` 要能覆盖。

**中立动作**（OSWorld 协议，禁止 shell 字段进入本实验）：

```text
click {x, y, button?}
double_click {x, y}
move {x, y}
scroll {x, y, dx, dy}
type {text}
key {keys}          # e.g. ["ctrl", "s"]
wait {seconds}
terminate {status}  # success | fail
```

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
bench                osworld_verified
bench_commit         >= 091f5ef（见第 7 节）
task_id              5ea617a3-0e86-4ba6-aab2-dac9aa2e8d57
num_envs             1
max_steps            50
harness              deepseek_harness
harness_version      pin 的 deepseek-harness-sdk 版本
cordis_config        configs/dsh/osworld_gui_only.cordis.yml
model.backend        openai_compat
model.endpoint_kind  api（前期）
model.name           选定的小 Qwen VLM，可改字段
model.api_key_env    环境变量名
protocol.observation     screenshot
protocol.guest_actions   mouse_keyboard
protocol.harness_bash    false
```

无 GPU / 无端点时不要默认改成 dummy 还报「已验证模型」；应让 `doctor` 失败。

模型密钥：`api_key_env` 只写环境变量名。

---

## 6. 模型后端与 Harness

`ModelBackend.complete(...)`：

- `dummy`：平台自测，**不是**推理验证。
- `openai_compat`：接 Qwen 小 VLM（DashScope / 本地 vLLM / 集群网关）。不绑定 4090。

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

**版本 pin 与预发布陷阱**：SDK 目前只有预发布版（如 `0.1.0rc7`）。`uv` 若用 `--prerelease=allow`，pydantic 会被一起拉成 beta（实测 `2.14.0b1`），这会污染整个平台的 schema 层。pyproject 里必须写：

```toml
[tool.uv]
prerelease = "if-necessary"
```

只有确实没有正式版的包才走预发布，pydantic 仍解析到稳定版（实测锁定 `deepseek-harness-sdk 0.1.0rc7` + `pydantic 2.13.4`）。不要用 `if-necessary-or-explicit`——uv 已把它标为弃用。dsh 版本号 pin 进 `smoke_osworld.yaml`。

### 6.2 自备 cordis.yml（阶段 1 的硬性前提）

`cordis.yml` 是 dsh 的插件组合清单。**禁止用 SDK 的零配置默认组合**：它挂了 `dsh-bash-local` / `dsh-subprocess-local` / `dsh-fs-local`，一启动就把 bash 交给模型，直接违反第 2 节的协议约束。

本仓库自备一份 `configs/dsh/osworld_gui_only.cordis.yml`，纳入版本控制，与 dsh 版本一起 pin。相对默认组合必须做三件事：

1. **不挂** `dsh-bash-local`、`dsh-subprocess-local`、`dsh-fs-local`。
2. **补挂** `dsh-attachment-local`。默认组合没有它，缺了截图进不了会话。
3. **挂** `dsh-llm-pi-ai` 声明模型 route（见 6.3），替换默认的 `dsh-llm-deepseek`。

上游**没有** computer-use 插件（已核实：仓库 9060 条路径中无任何 screenshot / 键鼠相关包），键鼠与截图工具必须自己提供。实现方式：本仓库写一个 Python MCP server 暴露第 4 节的中立动作，通过 dsh 的 `@deepseek-ai/dsh-mcp-client` 插件以 stdio 挂进 cordis.yml。这样键鼠代码留在本仓库，不必往 dsh 里写 TypeScript，且工具返回的图片是 dsh 官方支持的路径。

### 6.3 模型 route：一个插件两条路线

`dsh-llm-pi-ai` 是通用多 provider adapter，一个实例可持有多条 route；pi-ai 未内置的端点整份声明即可，OpenAI 兼容网关与自托管服务器都属于配置而非改代码。API 路线与本地路线因此是两条并列 route，切换只改实验 YAML 选哪条：

```yaml
- id: llm
  name: '@deepseek-ai/dsh-llm-pi-ai'
  config:
    providers:
      qwen-api:                    # endpoint_kind: api
        api: openai-completions
        baseURL: <云端 OpenAI 兼容地址>
        apiKeyEnv: <环境变量名>
        models: [{ id: <qwen-vl-model-id> }]
      qwen-local:                  # endpoint_kind: local（vLLM / 集群网关）
        api: openai-completions
        baseURL: <本地 OpenAI 兼容地址>
        apiKeyEnv: <环境变量名>
        models: [{ id: <qwen-vl-model-id> }]
```

`apiKeyEnv` 只写环境变量名，与第 2 节的密钥约束一致。自建网关请求形状有差异时用 pi-ai 的 `compat` 字段修正，不要改 adapter 代码。

**接图能力必须先验证再跑题**：pi-ai 文档称 input modalities 由其内置 catalog 提供，自建 route 不能声明该字段。接 adapter 的第一步是发一张截图做连通性测试，确认模型真的收到了图。若自建 route 不吃图，退路是改用 `dsh-llm-deepseek` 并把 `baseURL` 指向 Qwen 端点、给该 model 显式声明 `inputModalities: [text, image]`；代价是只剩一条固定 route（`deepseek-official`）且带 DeepSeek 专有 thinking 语义，因此只作退路。

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
- `macos` / `windows`：类存在（`benches/macos.py`、`benches/windows.py`），`prepare`/`run_trial` raise `UnsupportedBenchError`。

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

1. `pyproject.toml`（含 6.1 的 `[tool.uv] prerelease`）+ CLI help
2. `errors.py` + schema（compute_backend / harness 枚举、`protocol` 三开关、`limits`、`endpoint_kind`）与两份实验 YAML
3. dummy + stub + fake bench
4. `run` / `report` / `prune` / `doctor`
5. pytest（fake 路径 + 失败分类 + unsupported 分支）
6. `.github/workflows/ci.yml`
7. DeepSeek Harness adapter 骨架、`harness/desktop_mcp.py`、`configs/dsh/osworld_gui_only.cordis.yml`、OSWorld adapter（无资源则 skip 真跑）

---

更新 [README.md](README.md)：安装、`uv sync`、两条 smoke 命令、`doctor`。保留 docs 链接。

---

## 9. 测试

- 无网、无 Docker 必须绿：`uv run pytest`
- 标记 `@pytest.mark.osworld` 的测试默认 skip，除非 `CUA_EVAL_OSWORLD=1`
- 禁止在测试里下载 qcow2
- CI（`.github/workflows/ci.yml`）只跑 `uv sync` + `uv run pytest`：**不得**拉 VM 镜像、不得下载 qcow2、不得需要模型密钥

阶段 0 的 pytest 必须覆盖：schema 校验、CLI 假评测全链路、Table 1 渲染（含双指标与 ASR 方向）、retention 清理、四种失败分类、未实现 backend/bench 抛 `UnsupportedComputeBackend` / `UnsupportedBenchError`。

---

## 10. 推迟、不阻塞编码的项

实现时用合理默认，不要停下来问：

- 各 bench 安全语义
- 真实 Qwen+dsh+OSWorld 跑在哪类执行机：用阶段 0/`doctor` 的最小消耗再选，不在代码里写死 4090
- 选用哪一个具体 Qwen 小模型 id 与端点地址：`smoke_osworld.yaml` 里放可改字段，两条 route 的形状已由 6.3 定好
- harness 侧 bash 是否永久禁止：本阶段一律 `false`，不要自行打开

已关闭、**不要**再当成开放问题的项（见 [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md) 第 6 节）：模型接入路线、api/local 双路线、协议三开关、OSWorld 单题 id 与 commit pin、存储形态（无数据库）、指标口径、`max_steps=50`。上游 computer-use 插件**确认不存在**，按 6.2 自己写 MCP server，不要改成 coding bash 评测。

若与 REQUIREMENTS 冲突，以 REQUIREMENTS 为准并改本文。
