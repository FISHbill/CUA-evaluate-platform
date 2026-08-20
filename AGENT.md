# AGENT.md — CUA 评测平台开发手册

给后续编程 agent 的实现说明书。需求以 [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md) 为准；架构背景见 [docs/PLAN.md](docs/PLAN.md)。**不要重新讨论技术栈或范围**，按本文开工。

语言：对用户用中文；代码、CLI、标识符用英文。

---

## 1. 目标

搭建 Linux 上的 CUA 评测平台：比较 **模型 + harness** 在公开 bench 上的表现。结果表形态对齐 Qwen-CUA Table 1（单指标，或 `binary / partial`、`task success / ASR`）。

当前交付：**阶段 0 必须在 Cursor VM 完成**（fake + dummy，无 GPU）。**阶段 1** 是真实 CUA 推理验证：OSWorld-Verified **1 题** + **Qwen 小模型** + **DeepSeek Harness**。阶段 1 若本 VM 资源不够，只实现 adapter 与 `doctor` 探测，真正跑题放到后续选定的执行机。

### 阶段 0 完成标准（必须，Cursor VM，不依赖 GPU/Docker 镜像）

- 可安装的 Python 包 + CLI
- fake bench + **dummy** 模型 + stub harness：`cua-eval run` 能跑完 1 题并写出结果目录
- `cua-eval report` 打出 Table 1 风格文本（含双指标列、ASR 标注越低越好）
- `ComputeBackend` 枚举存在，仅 `local_linux` 可运行；其余 raise `UnsupportedComputeBackend`
- `pytest` 覆盖 schema、CLI、假评测、失败分类；默认 CI **禁止**拉 VM 镜像

### 阶段 1 完成标准（真实推理验证）

- OSWorld-Verified **1 题**，`num_envs=1`
- Agent = **Qwen 小尺寸 VLM**（OpenAI 兼容 `base_url`）+ **DeepSeek Harness**
- 对桌面只暴露截图 + 键鼠；不把 dsh 默认 bash 算进本实验协议
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
| OSWorld 协议：截图 + 键鼠 | 本阶段把 dsh 的 bash/编辑器算进分数 |
| Mac/Windows **bench 客户机** 标 `unsupported` | 在 Linux 上假跑 macOS 分数 |
| 本地 `results/` + `artifact_retention_days`（默认 14） | 本阶段上 MinIO/S3 |
| 密钥只从环境变量读 | API key 进 git / YAML / 桌面 VM |

非目标：训练、官方 leaderboard 代跑、复现论文绝对分数、本阶段跑满 8 个 bench。

---

## 3. 仓库结构（按此创建）

```text
AGENT.md
README.md
pyproject.toml
configs/experiments/smoke_fake.yaml
configs/experiments/smoke_osworld.yaml
src/cua_eval/
  __init__.py
  cli.py                 # cua-eval
  schema.py              # Experiment, AgentSpec, Metric, RunRecord
  backends/compute.py    # ComputeBackend: local_linux | windows_pc | cloud_single | small_cluster | gpu_cluster
  backends/model.py      # dummy | openai_compat（Qwen 走兼容接口）
  harness/stub.py        # CI 用，配合 dummy
  harness/deepseek.py    # DeepSeek Harness adapter（阶段 1）
  orchestrator/run.py
  store/results.py       # 写 results/、按 retention 清理
  report/table1.py
  benches/base.py        # BenchAdapter protocol
  benches/fake.py
  benches/osworld.py     # 阶段 1；阶段 0 可先 stub
  actions.py             # 中立键鼠 schema → OSWorld pyautogui 映射
tests/
  test_schema.py
  test_cli_fake.py
  test_table1.py
  test_retention.py
docs/                    # 已有 PLAN / REQUIREMENTS / RESOURCES，勿删
```

包名：`cua_eval`。入口：`cua-eval`。

---

## 4. 核心类型

评测对象主键：`(model, harness, protocol, bench, bench_version, compute_backend)`。

- 阶段 0：`harness=stub`，`model.backend=dummy`，`compute_backend=local_linux`
- 阶段 1：`harness=deepseek_harness`，`model.backend=openai_compat`（Qwen 小 VLM），`bench=osworld_verified`

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

**Metric**：`name`, `value: float`, `higher_is_better`, `unit?`。OSWorld-Verified 用 `success_rate`；Table 1 渲染器必须能显示 `a / b`（为 2.0 / RedTeamCUA 预留），即使 smoke 只有单值。

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

---

## 5. CLI

```text
cua-eval doctor              # 查 python/docker/kvm/磁盘/模型后端
cua-eval run -c <yaml>       # 跑实验
cua-eval report <run_id>     # Table 1 文本到 stdout，并写 results/<run_id>/table1.md
cua-eval prune               # 按配置清理过期产物
```

`smoke_fake.yaml`：`bench: fake`，`model.backend: dummy`，`harness: stub`，`compute_backend: local_linux`。

`smoke_osworld.yaml`：`bench: osworld_verified`，1 个 `task_id`，`num_envs: 1`，`harness: deepseek_harness`，`model.backend: openai_compat`，`model.name` 为选定的小 Qwen VLM。无 GPU 时不要默认改成 dummy 还报「已验证模型」；应让 `doctor` 失败。

模型密钥：`api_key_env` 只写环境变量名。

---

## 6. 模型后端与 Harness

`ModelBackend.complete(...)`：

- `dummy`：平台自测，**不是**推理验证。
- `openai_compat`：接 Qwen 小 VLM（DashScope / 本地 vLLM / 集群网关）。不绑定 4090。

`Harness`：

- `stub`：配合 dummy，保证循环结束。
- `deepseek_harness`：包装 [deepseek-ai/deepseek-harness](https://github.com/deepseek-ai/deepseek-harness)。pin 版本写入配置。对 OSWorld 只挂截图/键鼠工具。dsh 是 Node 项目：用官方 CLI/API 或 subprocess，不要把整个 dsh 源码抄进本仓库。

`ComputeBackend.run_job`：本阶段 `local_linux` 即当前进程。`windows_pc` / `cloud_single` / `small_cluster` / `gpu_cluster` 仅 stub。

截图历史最多 20 张。本阶段不必做 chunked folding。

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
- `osworld_verified`：调用官方 Docker provider，**pin 官方仓库 commit**（写入配置，不要浮动 `main`）。不要把 OSWorld 源码复制进本仓库；checkout 到 `third_party/OSWorld`（gitignore 大镜像）。
- `macos` / `windows`：类存在，`prepare`/`run_trial` raise `UnsupportedBenchError`。

阶段 1 选官方任务时优先 **本地、短、少出网** 的 OS/GIMP 类；具体 id 在实现时从 `evaluation_examples` 挑一个并写进 `smoke_osworld.yaml`。

---

## 8. 实现顺序（一次做完再停）

1. `pyproject.toml` + CLI help
2. schema（含 compute_backend / harness 枚举）与 YAML
3. dummy + stub + fake bench
4. `run` / `report` / `prune` / `doctor`
5. pytest（fake 路径）
6. DeepSeek Harness adapter 骨架 + OSWorld adapter（无资源则 skip 真跑）

---

更新 [README.md](README.md)：安装、`uv sync`、两条 smoke 命令、`doctor`。保留 docs 链接。

---

## 9. 测试

- 无网、无 Docker 必须绿：`uv run pytest`
- 标记 `@pytest.mark.osworld` 的测试默认 skip，除非 `CUA_EVAL_OSWORLD=1`
- 禁止在测试里下载 qcow2

---

## 10. 推迟、不阻塞编码的项

实现时用合理默认，不要停下来问：

- 各 bench 安全语义
- 真实 Qwen+dsh+OSWorld 跑在哪类执行机：用阶段 0/`doctor` 的最小消耗再选，不在代码里写死 4090
- 选用哪一个具体 Qwen 小模型 id：实现时在 `smoke_osworld.yaml` 放可改字段，默认选公开的小尺寸 VL
- OSWorld 官方 commit pin：接 adapter 时写入 yaml
- DeepSeek Harness 的 computer-use 插件若上游尚未提供：在 adapter 里用最小截图/键鼠工具桥接到 OSWorld，不要改成 coding bash 评测

若与 REQUIREMENTS 冲突，以 REQUIREMENTS 为准并改本文。
