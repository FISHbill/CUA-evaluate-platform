# 执行计划

把 [AGENTS.md](../AGENTS.md) 第 8 节的粗粒度顺序展开成可逐条验收的任务。需求以 [REQUIREMENTS.md](./REQUIREMENTS.md) 为准，实现细节以 AGENTS.md 为准；**本文只负责顺序、完成判据和验证方式，不重复技术方案**。

每个任务给三样东西：交付物、完成判据、怎么验证。不写日历工期——任务的难度用「改哪些模块、依赖什么、卡在哪」描述。

---

## 1. 里程碑总览

| 里程碑 | 内容 | 执行机 |
| --- | --- | --- |
| M0 | 包骨架与类型层 | Cursor VM |
| M1 | 假链路端到端跑通 | Cursor VM |
| M2 | 报表与产物治理 | Cursor VM |
| M3 | 测试与 CI 质量门 | Cursor VM |
| M4 | dsh 接入（含 mock 端点冒烟） | Cursor VM |
| M5 | OSWorld adapter（写代码，不真跑） | Cursor VM |
| M6 | 真实单题验证 | **必须换执行机** |

M0→M3 就是 AGENTS.md 的**阶段 0 完成标准**。M4→M6 是**阶段 1**。M6 之前的所有代码都能在当前 Cursor VM 上写完并验证到「除了真桌面和真模型之外全都跑过」的程度。

依赖关系：

```text
M0 ──> M1 ──> M2 ──┐
        │           ├──> M3
        │           │
        └───────────┴──> M4 ──> M5 ──> M6（换机）
```

M2 与 M4 在 M1 之后可以并行；M3 的测试随 M0–M2 增量补，不要攒到最后。

---

## 2. M0 — 包骨架与类型层

### T0.1 `pyproject.toml` 与 CLI 骨架

- **交付物**：`pyproject.toml`、`src/cua_eval/__init__.py`、`src/cua_eval/cli.py`（Typer，四个子命令只有 `--help`）。
- **判据**：Python ≥ 3.11；依赖含 Pydantic v2、Typer、PyYAML；`[tool.uv] prerelease = "if-necessary"`（理由见 AGENTS.md 6.1，漏了会把 pydantic 拉成 beta）；入口点 `cua-eval`；配好 ruff + mypy。
- **验证**：`uv sync` 成功；`uv run cua-eval --help` 列出 `doctor` / `run` / `report` / `prune`；`uv run ruff check .` 与 `uv run mypy src` 通过。

### T0.2 `errors.py`

- **交付物**：`UnsupportedComputeBackend`、`UnsupportedBenchError`、`ConfigError`、`HarnessError`、`ModelError`、`InfraError`。
- **判据**：全部继承同一个 `CuaEvalError` 基类；异常与 AGENTS.md 第 4 节的失败类可互相映射。
- **验证**：随 T3.1 的 `test_unsupported.py` 一起验。

### T0.3 `schema.py`

- **交付物**：`Experiment`、`AgentSpec`、`ModelSpec`、`Protocol`、`Limits`、`Metric`、`MetricSet`、`TrialResult`、`RunRecord`，以及枚举 `ComputeBackend` / `HarnessId` / `BenchId` / `EndpointKind` / `FailureClass`。
- **判据**：
  - `Protocol` 是 AGENTS.md 2.1 的四个独立字段；`guest_shell` 可为 `True`，`harness_shell` 默认且保持 `False`。
  - `Limits.max_steps` 默认 50，`num_envs` 默认 1，另有整题 wall-clock 超时。
  - `ModelSpec` 含 `backend` / `endpoint_kind` / `provider_route` / `name` / `api_key_env` / `input_modalities`；**跨字段校验：`protocol.observation=screenshot` 时 `input_modalities` 必须含 `image`**，`observation=text` 时不要求，两者矛盾的组合直接拒绝配置。
  - 主键 `(model, endpoint_kind, harness, protocol, bench, bench_version, compute_backend)` 能从 `RunRecord` 还原。
  - `api_key_env` 只接受环境变量名，出现形似密钥的值要报错。
- **验证**：`tests/test_schema.py` 覆盖默认值、三开关、image modality 强制、密钥字段拒绝字面值。

### T0.4 两份实验 YAML

- **交付物**：`configs/experiments/smoke_fake.yaml`、`configs/experiments/smoke_osworld.yaml`，字段按 AGENTS.md 第 5 节。
- **判据**：两份都能通过 schema 校验；`smoke_osworld.yaml` 里厂商相关的值（`provider_route` / `name` / `api_key_env` / `baseURL`）都是可改字段，**不得把某一家写死在代码里**。
- **验证**：测试里加载这两份 YAML 并构造 `Experiment` 成功。

---

## 3. M1 — 假链路端到端跑通

### T1.1 `actions.py`

- **交付物**：AGENTS.md 第 4 节的中立动作类型，以及到 OSWorld pyautogui 的映射。
- **判据**：坐标统一用**像素**并与截图尺寸一起存；动作里不存在任何 shell / 脚本字段。
- **验证**：单测覆盖每种动作的序列化与映射；一条断言确保动作模型不含 shell 类字段。

### T1.2 `backends/model.py` 的 `dummy`

- **判据**：不读截图，返回固定动作序列并以 `terminate` 收尾，保证循环必然结束。**dummy 的分数不得被当成模型能力**，`RunRecord` 里要能一眼看出这是 dummy。

### T1.3 `harness/base.py` + `harness/stub.py`

- **判据**：`Harness` 协议定义清楚「给观测、收动作」的边界；`stub` 配合 dummy 走完循环。

### T1.4 `benches/base.py` + `benches/fake.py`

- **判据**：`BenchAdapter` 五个方法按 AGENTS.md 第 7 节；`fake` 用内存任务、64×64 纯色 PNG，evaluator 对 dummy 给固定分。不启动任何 VM、不出网。

### T1.5 `backends/compute.py`

- **判据**：五个枚举值都存在，仅 `local_linux` 可运行（当前进程），其余 raise `UnsupportedComputeBackend`。

### T1.6 `orchestrator/run.py`

- **判据**：步循环遵守 `max_steps` 与 wall-clock 超时；异常按四类归档——环境问题 `infra_error`、模型问题 `model_error`、任务没做成 `task_fail`、成功 `ok`；**`infra_error` 绝不能记成 0 分**。

### T1.7 `store/results.py`

- **判据**：目录形状与 AGENTS.md 第 4 节一致；`run_id` 用时间戳加短随机、不会覆盖；`result.json` 存逐题 0.0–1.0 原始分与失败类；`trace.jsonl` 每步记 observation 引用、动作、token，并记**本步实际送入模型的截图张数**。

### T1.8 `cua-eval run`

- **验证（M1 的总验收）**：`uv run cua-eval run -c configs/experiments/smoke_fake.yaml` 在无网、无 Docker 的机器上跑完 1 题并写出完整结果目录。

---

## 4. M2 — 报表与产物治理

### T2.1 `report/table1.py`

- **判据**：单值列与 `a / b` 双指标列都能渲染；ASR 类指标标注「越低越好」并在排序时反向；`infra_error` 不进成功率分母时要在表里写清（例如 `2/2 scored, 1 infra skipped`）；聚合值用百分数、`unit="percent"`，逐题仍是 0.0–1.0；行标识里能区分 `endpoint_kind`。
- **验证**：`tests/test_table1.py` 用构造好的 `MetricSet` 断言渲染文本，覆盖单值、双指标、ASR 反向、infra 跳过四种情况。

### T2.2 `cua-eval report`

- **判据**：打到 stdout 同时写 `results/<run_id>/table1.md`。

### T2.3 `cua-eval prune`

- **判据**：按 `artifact_retention_days`（默认 14）删过期 `results/<run_id>`，不误删未过期目录，不越界删到 `results/` 之外。
- **验证**：`tests/test_retention.py` 用伪造 mtime 的目录验证边界。

### T2.4 `cua-eval doctor`

- **判据**：分项报告 Python 版本、`uv`、Docker、`/dev/kvm`**当前用户可读性**、磁盘余量、模型端点；缺项时**退出非 0 并说明缺什么**，绝不能静默降级成 dummy 还宣称「已验证模型」。此外必须校验选中的 `provider_route` 声明了 `image`，纯文本 route 直接判失败。
- **验证**：在当前 Cursor VM 上跑 `doctor` 应当明确报出「无 Docker、`/dev/kvm` 不可读、CPU/内存低于 OSWorld 档」并非 0 退出——这台机器本身就是这条路径的天然用例。

---

## 5. M3 — 测试与 CI 质量门

### T3.1 测试套件

- **交付物**：`test_schema.py`、`test_cli_fake.py`、`test_table1.py`、`test_retention.py`、`test_failure_classes.py`、`test_unsupported.py`。
- **判据**：四种失败分类各有用例；未实现的 backend / bench 抛对应异常；标 `@pytest.mark.osworld` 的用例默认 skip，除非 `CUA_EVAL_OSWORLD=1`；测试里不下载 qcow2、不出网、不需要密钥。

### T3.2 `.github/workflows/ci.yml`

- **判据**：只做 `uv sync` + `uv run ruff check` + `uv run mypy src` + `uv run pytest`；**不拉 VM 镜像、不下载 qcow2、不需要模型密钥**。
- **验证**：`uv run pytest` 在断网环境下全绿；CI 首次运行通过。

---

## 6. M4 — dsh 接入

这一段在当前 VM 上能做到「除了真模型和真桌面，其余全部跑过」。

### T4.1 依赖 pin

- **判据**：`deepseek-harness-sdk` 写进依赖并 pin 版本；锁文件里 `pydantic` 必须是稳定版。
- **验证**：`uv lock` 后检查锁文件中 `pydantic` 不带 `a/b/rc` 后缀。

### T4.2 `harness/desktop_mcp.py`

- **交付物**：一个 MCP server（stdio），把 T1.1 的中立动作暴露成工具，工具集由 `protocol` 决定。
- **判据**：
  - `observation=screenshot` 时提供截图工具，输出 **JPEG、1920×1080**（AGENTS.md 6.4 的两道上限决定了这个格式）。
  - `guest_shell=true` 时提供 `shell` 工具，底层调 OSWorld `PythonController.run_bash_script()`，命令在**桌面 VM 内**执行。**绝不能**改成在评测宿主机上执行——理由见 AGENTS.md 2.1。
  - `guest_shell=false` 时 `shell` 工具**完全不注册**，而不是注册了再拒绝。
  - 任何情况下都不提供宿主机文件系统能力。
- **验证**：脱离 dsh 单独启动该 server，用一个最小 MCP 客户端列工具，断言工具集随 `protocol` 变化（四种开关组合各验一次）；调一次 `shell` 断言命令确实落在 guest 而非宿主机（例如比对 `hostname` 或某个只存在于 guest 的路径）。

### T4.3 `configs/dsh/osworld.cordis.yml`

- **判据**：**不挂** `dsh-bash-local` / `dsh-subprocess-local` / `dsh-fs-local`——**`guest_shell=true` 也不挂**，guest 内的 shell 由 T4.2 的 MCP 工具提供；**挂** `dsh-attachment-local`、`dsh-llm-pi-ai`、`dsh-mcp-client`；显式写出 `maxImageDimension`、`maxRequestImageBytes`；截图协议的 route 上写 `defaultInput: [text, image]`，纯文本协议的 route 不写。
- **验证**：加一个测试解析该 YAML 并断言「dsh 宿主 shell 插件一个都不在、必需插件一个都不缺、route 的 modality 声明与 `protocol.observation` 一致」。这条测试有两层作用：防止有人换回 dsh 零配置默认组合，也防止有人把「允许 bash」误实现成挂 `dsh-bash-local`。

### T4.4 `harness/deepseek.py`

- **判据**：用官方 Python SDK 的 `DeepSeekHarness` 驱动，注入 T4.3 的 cordis 配置；把 `provider_route` / `name` 映射成 dsh 的 provider / model；`protocol` 决定注册哪些 MCP 工具；截图协议下**截图历史 20 张的上限由平台侧裁剪**并写进 `trace.jsonl`，不依赖 dsh 的静默 offload。dsh 相关的 API 调用集中在这一个文件里，便于 SDK 预发布版变动时收敛改动面。

### T4.5 mock 端点冒烟（本 VM 的最大验证边界）

- **交付物**：一个本地 OpenAI 兼容 mock 服务，返回固定动作。
- **判据**：两条协议各跑一遍闭环——
  - **shell 协议**：`MCP shell → dsh → mock 模型 → 动作回到 MCP`，断言 mock 侧收到的是纯文本请求、且 `shell` 工具在工具清单里。
  - **截图协议**：断言 mock 侧确实收到了 `image_url` 部分，即**图片真的进了模型请求**。
- **为什么必须做**：这是在没有真模型、没有真桌面的情况下唯一能证伪「cordis 配置错、modality 漏声明、图片没进请求、shell 落错机器」这四类问题的手段。M6 换机后再发现的代价高得多。

---

## 7. M5 — OSWorld adapter

### T5.1 `benches/osworld.py`

- **判据**：`third_party/OSWorld` 走 checkout 并 pin commit ≥ `091f5ef`（低于此版本每题泄漏约 32 GB 匿名卷）；调官方 evaluator，**不重写打分逻辑**；`normalize` 把官方分数原样转成 0.0–1.0；`prepare` 幂等；`cleanup` 确保容器与匿名卷都被回收。

### T5.2 `benches/macos.py` / `benches/windows.py`

- **判据**：类存在，`prepare` / `run_trial` raise `UnsupportedBenchError`。绝不在 Linux 上假跑 macOS 分数。

### T5.3 `doctor` 的 OSWorld 前置检查

- **判据**：检查 Docker 可用、`/dev/kvm` 当前用户可读、磁盘余量 ≥ RESOURCES.md 第 6 节的建议值、镜像是否已就位；缺项逐条报出。

### T5.4 集成测试骨架

- **判据**：标 `@pytest.mark.osworld`，默认 skip，`CUA_EVAL_OSWORLD=1` 时才尝试真跑。

---

## 8. M6 — 真实单题验证（换机）

首轮配置（已确认）：`model.name=Qwen2.5-VL-7B-Instruct`、`observation=screenshot`、`guest_actions=mouse_keyboard`、`guest_shell=true`、`harness_shell=false`。

**前置条件**（任一不满足就不要开始）：

| 条件 | 要求 |
| --- | --- |
| 执行机 | 8 vCPU、32 GB RAM、`/dev/kvm` 当前用户可读、≥ 150 GB 可用盘、Docker 可用 |
| 模型端点 | `Qwen2.5-VL-7B-Instruct` 的 OpenAI 兼容端点 + 密钥，由使用方在环境搭好后提供 |
| 密钥 | 通过环境变量提供，不进 git / YAML / 桌面 VM |
| 上下文容量 | 自托管时 vLLM 的 `--max-model-len` 必须够放「截图历史深度 × 约 2,700 token」，见 AGENTS.md 6.4 |

### T6.1 环境就绪

- **判据**：`cua-eval doctor` 全绿。走 `endpoint_kind=local` 时 `doctor` 还要读出端点实际的 `max_model_len` 并与配置的截图历史深度核对，不够就报错而不是放行。

### T6.2 接图连通性测试

- **判据**：发一张截图，确认模型端**真的收到了图**并返回与图像内容相关的响应。声明正确但端点不支持图只会在跑题中途才炸，所以必须单独做、单独确认，**不要凭型号名假设**。

### T6.3 guest shell 落点验证

- **判据**：通过 `shell` 工具执行一条命令，确认它**落在桌面 VM 内而不是评测宿主机上**（例如读取一个只存在于 guest 的路径，或比对 hostname）。落错机器的话题目永远解不了，而且等于把评测设施的 shell 交给了被评测的模型——理由见 AGENTS.md 2.1。这一条与 T6.2 都是跑题前的独立门禁。

### T6.4 跑单题

- **判据**：`5ea617a3-0e86-4ba6-aab2-dac9aa2e8d57`、`num_envs=1`、`max_steps=50`，产出 `result.json` / `trace.jsonl` / `screenshots/` / `table1.md`。环境侧失败记 `infra_error`，**不得记成模型 0 分**。分数高低不是验收条件——**跑通闭环才是**。
- **报表判据**：`protocol` 四个开关照实写进结果。这一列是「截图 + 键鼠 + guest shell」协议下的成绩，**不可**与 Qwen-CUA 论文 Table 1 的 screenshot-only 数字并列比较。

### T6.5 回填最小验证消耗

- **判据**：把实测的墙钟、token 用量（区分视觉与文本）、磁盘占用、reset 耗时回填进 [RESOURCES.md](./RESOURCES.md)，作为后续选执行机（Windows PC / 云单机 / 集群）的依据。

### 可选旁支：纯文本协议列

`observation=text` 是 schema 支持的合法取值，给纯文本模型留的一列（例如 dsh 出厂 catalog 里未声明 `image` 的 `deepseek-v4-flash`）。它不需要视觉模型也不需要截图管线，可作为对照列回答「没有 GUI 只有 shell 能做到多少」。**只改配置不改代码**——如果这一步需要改代码，说明协议开关没做成真正独立的字段，回去修 M0/M4。

---

## 9. 风险与已定的应对

| 风险 | 应对 | 何时能确认 |
| --- | --- | --- |
| 端点声明了 image 但实际不支持 | 单独做 T6.2，不与跑题混在一起 | M6 |
| **bash 被误实现成宿主机 shell** | 只用 MCP 的 `shell` 工具转调 OSWorld `run_bash_script`；T4.3 断言不挂 `dsh-bash-local`；T6.3 实测落点 | M4 与 M6 |
| **7B 上下文放不下截图历史** | 历史深度做成配置项；`doctor` 核对端点 `max_model_len`；必要时降深度而非降分辨率 | M6 |
| 下采样截图导致点击系统性偏移 | 坐标按缩放比映射回原图，且在 `trace.jsonl` 记原图尺寸 | M4 |
| dsh SDK 是预发布版，API 可能变 | pin 版本；dsh 调用集中在 `harness/deepseek.py` | 持续 |
| 有人换回 dsh 零配置默认组合，宿主 bash 悄悄回来 | T4.3 的断言测试作护栏 | M4 |
| 截图撞 20 MiB 请求上限被静默丢弃 | JPEG + 1920×1080 + 平台侧裁剪并记 trace | M4 |
| OSWorld 旧版本每题泄漏约 32 GB | commit pin 下限 `091f5ef`；`cleanup` 回收匿名卷 | M5 |
| 模型接入被写成只服务一家厂商 | 厂商中立是硬约束；`provider_route` 是唯一入口，代码里不得有厂商分支 | M0 与 code review |

---

## 10. 验收清单

阶段 0（M0–M3）：

- [ ] `uv sync` 与 `uv run cua-eval --help` 可用
- [ ] `cua-eval run -c smoke_fake.yaml` 在无网无 Docker 下跑完并写出结果目录
- [ ] `cua-eval report` 打出 Table 1 风格文本并写 `table1.md`
- [ ] `cua-eval prune` 按 retention 正确清理
- [ ] `cua-eval doctor` 在本 VM 上正确报出缺 Docker / KVM 不可读并非 0 退出
- [ ] 仅 `local_linux` 可运行，其余 backend 抛 `UnsupportedComputeBackend`
- [ ] `uv run pytest` 全绿且不出网、不拉镜像
- [ ] CI 通过

阶段 1（M4–M6）：

- [ ] cordis.yml 的护栏测试通过（**无 `dsh-bash-local`**、有 attachment、已声明 image）
- [ ] MCP server 独立自测通过：截图为 JPEG 1920×1080，工具集随 `protocol` 变化
- [ ] `shell` 工具在 `guest_shell=false` 时完全不注册
- [ ] mock 端点冒烟证明图片真的进了模型请求
- [ ] OSWorld adapter pin ≥ `091f5ef`，`@pytest.mark.osworld` 默认 skip
- [ ] 换机后 `doctor` 全绿，且已核对端点 `max_model_len` 与截图历史深度
- [ ] 接图连通性测试通过（确认模型真收到图）
- [ ] guest shell 落点验证通过（命令确实在桌面 VM 内执行）
- [ ] 单题跑通，失败按四类正确归档
- [ ] `protocol` 四开关照实写进结果，未与 screenshot-only 数字混列
- [ ] 最小验证消耗已回填 RESOURCES.md
