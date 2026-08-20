# AGENT.md — CUA 评测平台开发手册

给后续编程 agent 的实现说明书。需求以 [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md) 为准；架构背景见 [docs/PLAN.md](docs/PLAN.md)。**不要重新讨论技术栈或范围**，按本文开工。

语言：对用户用中文；代码、CLI、标识符用英文。

---

## 1. 目标

搭建 Linux 上的 CUA 评测平台：比较 **模型 + harness** 在公开 bench 上的表现。结果表形态对齐 Qwen-CUA Table 1（单指标，或 `binary / partial`、`task success / ASR`）。

当前交付只到 **阶段 0 + 阶段 1 smoke**，不是八个 bench 全量。

### 阶段 0 完成标准（必须，不依赖 Docker/KVM/GPU）

- 可安装的 Python 包 + CLI
- fake bench + dummy 模型：`cua-eval run` 能跑完 1 题并写出结果目录
- `cua-eval report` 打出 Table 1 风格文本（含双指标列、ASR 标注越低越好）
- `pytest` 覆盖 schema、CLI、假评测、失败分类；默认 CI **禁止**拉 VM 镜像

### 阶段 1 完成标准（有 Docker+KVM 时）

- OSWorld-Verified **1 题**，`num_envs=1`
- 协议：截图 in → OpenAI 兼容模型（或 dummy）→ 键鼠 out → 官方 evaluator
- 环境失败记 `infra_error`，不得记成模型 0 分
- 无 KVM/镜像时：`cua-eval doctor` 说明缺什么并退出非 0，不要假装跑过

---

## 2. 硬约束（违反即做错）

| 做 | 不要做 |
| --- | --- |
| Python 3.11+、uv、Pydantic v2、Typer、YAML 实验配置 | Web 前端、多用户、FastAPI 控制面 |
| 仅 harness `native-cua`（截图 + 键鼠） | Bash、DOM、a11y、厂商 Computer Use API、OpenClaw |
| 模型走 `ModelBackend`：`dummy` 或 OpenAI 兼容 HTTP | 把 vLLM/厂商 SDK 写进评测循环 |
| 官方 evaluator 打分；adapter 只包装 | 重写 OSWorld 打分逻辑 |
| 单机 `num_envs=1`；调度接口可扩展 | 本阶段实现 K8s/集群执行 |
| Mac/Windows adapter **接口预留**，实现里明确 `unsupported` | 在 Linux 上假跑 macOS 分数 |
| 本地 `results/` + `artifact_retention_days`（默认 14） | 本阶段上 MinIO/S3 |
| 密钥只从环境变量读 | 把 API key 写进 git、YAML、桌面 VM |

非目标：训练、RL rollout、官方 leaderboard 代跑、复现论文绝对分数。

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
  backends/model.py      # ModelBackend: dummy | openai_compat
  harness/native_cua.py  # 截图历史 + 解析键鼠动作
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

评测对象主键：`(model, harness, protocol, bench, bench_version)`。本阶段 `harness=native-cua`，`protocol.observation=screenshot`，`protocol.allow_bash=false`。

**中立动作**（模型输出 JSON，禁止 shell 字段）：

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

`smoke_fake.yaml` 要点：`bench: fake`，`model.backend: dummy`，`max_steps: 3`，`task_ids: [fake-001]`。

`smoke_osworld.yaml`：`bench: osworld_verified`，`task_ids` 只含 1 个官方任务 id，`num_envs: 1`，`model.backend: dummy` 或 `openai_compat`（`base_url`/`api_key_env`/`model`）。

模型密钥：`api_key_env: OPENAI_API_KEY` 这种 **环境变量名**，不要把 key 写进 YAML。

---

## 6. 模型后端

`ModelBackend.complete(messages, images) -> text`

- `dummy`：忽略图，返回固定 `terminate` 或简单 `wait`，保证循环能结束。
- `openai_compat`：Chat Completions，多模态图用 data URL 或官方 vision 格式；超时、429 → `model_error` 可重试有限次。

以后集群只换 `base_url`，不要新造 RPC。本阶段不要实现 vLLM 启动脚本（可在 README 留一行示例命令）。

截图历史：最多保留 **20** 张（与 Qwen-CUA 主设定对齐）；超出则丢掉最旧的图、保留动作文本。本阶段不必做 chunked folding。

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

1. `pyproject.toml` + 空包 + `cua-eval --help`
2. schema 与 YAML 加载校验
3. dummy backend + native-cua 循环 + fake bench
4. `run` / `report` / `prune`
5. pytest（fake 路径）
6. `doctor` + OSWorld adapter smoke（机器允许才真正跑 VM；默认测试跳过）

更新 [README.md](README.md)：安装、`uv sync`、两条 smoke 命令、`doctor`。保留 docs 链接。

---

## 9. 测试

- 无网、无 Docker 必须绿：`uv run pytest`
- 标记 `@pytest.mark.osworld` 的测试默认 skip，除非 `CUA_EVAL_OSWORLD=1`
- 禁止在测试里下载 qcow2

---

## 10. 推迟、不阻塞编码的项

实现时用合理默认，不要停下来问：

- 各 bench 安全语义：本阶段出网允许；RedTeamCUA 以后再隔离
- 4090/云主机规格：代码只认 OpenAI 兼容 URL
- OSWorld 任务 pin 的精确 commit：实现阶段 1 时查官方 Verified 文档并写入 yaml
- Gym-Anything 子集、MyPCBench 主指标：未做那些 adapter 前不用定
- 许可证：不要提交官方 VM 镜像

若与 REQUIREMENTS 冲突，以 REQUIREMENTS 为准并改本文。
