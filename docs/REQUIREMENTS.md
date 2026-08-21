# 已确认需求（2026-08-19，2026-08-20 修订）

对照 [PLAN.md](./PLAN.md) 第 8 节默认假设：整体没有大的方向分歧。下面是本次确认后的约束，后续实现以本文为准。2026-08-20 补充：真实推理验证用 **Qwen 小模型 + DeepSeek Harness**；计算资源接口不绑定 4090。2026-08-20 二次确认：关闭了模型接入路线、单题 id、协议边界、存储形态、指标口径、步数上限六项待定项，见第 6 节。

---

## 1. 范围与成功标准

| 项 | 确认 |
| --- | --- |
| Bench 目标 | Table 1 那 **8 个**（OSWorld-Verified、OSWorld 2.0、MyPCBench、MacAgentBench、Gym-Anything、ScienceBoard、WebArena、RedTeamCUA） |
| 当前阶段 | 开发在 **Cursor VM**。分两层「最小」：平台骨架用 fake；**真实 CUA 推理验证** 用 OSWorld-Verified **1 题** |
| 最小真实 bench | **是**：在 Table 1 的 8 个里，OSWorld-Verified 单任务是资源最低的真实桌面 CUA 闭环 |
| 成功标准 | **内部可复现对比**；不追求对齐论文分数。模型可能较小，能跑完循环即可 |
| 交互 | **仅 CLI**；不做 Web 看板、不做多用户 |
| 协议 | 对 OSWorld smoke：**截图 + 键鼠**，禁止 a11y / DOM / 软件 API；agent 在桌面 VM 里开终端打字**允许**。详见第 6.3 节 |
| Mac / Windows bench 客户机 | 不跑；Windows **宿主机** 见 ComputeBackend 预留 |
| 出网 | 评测环境 **允许出网**（后续按 bench 再收紧安全语义） |

### 为什么 smoke 选 OSWorld-Verified，而不是 WebArena

WebArena 看起来「只是浏览器」，但官方站点镜像下载约 **180 GB**、盘要按 **1 TB** 准备。OSWorld-Verified 只要一份 Ubuntu 桌面盘：压缩包约 **11.4 GB**，就能做完整的「截图 → 模型 → 键鼠 → 官方打分」闭环。OSWorld 2.0、ScienceBoard、MyPCBench、RedTeamCUA 都更重。

骨架阶段仍先用 **fake bench**（不启动 VM）保证 CI；接到真实环境时用 OSWorld 一题。

---

## 2. Dummy、真实模型、Harness

**Dummy 不是评测对象。** 它是平台自测用的假模型：不读截图，返回固定动作（如 `wait` / `terminate`），用来在无 GPU、无密钥时验证「配置 → 循环 → 写结果 → 报表」是否通。Cursor VM 上的阶段 0 / `pytest` 走 dummy。

**真实推理验证**必须是：

```text
Qwen 系列小尺寸多模态模型  +  DeepSeek Harness（dsh）  +  OSWorld 1 题
```

- **模型**：Qwen 小尺寸 VLM（具体 id 写 YAML，如经 OpenAI 兼容接口的 `Qwen2.5-VL-*` / 后续 Qwen-VL）。不要写死某一张卡。
- **Harness**：`deepseek-harness`（[deepseek-ai/deepseek-harness](https://github.com/deepseek-ai/deepseek-harness)）。平台用 adapter 调它，不把评测循环写成只服务自研 `native-cua`。
- **注意**：dsh 上游默认组合就挂着 bash（`dsh-bash-local`），而且**没有** computer-use 插件。因此必须自备 cordis.yml 去掉 bash 并自己提供截图/键鼠工具，实现细节见 [AGENTS.md](../AGENTS.md) 第 6 节。

Cursor VM 通常无 GPU：阶段 0 只保证 dummy + fake；真实 Qwen+dsh+OSWorld 放到测完最小消耗后再选的机器上跑。

---

## 3. 运行形态与资源接口

作业跑在哪台机器，和 bench 里的客户机操作系统是两件事。OSWorld 客户机仍是 **Ubuntu 桌面 VM**。下面是 **评测作业宿主机**，必须做成可替换的 `ComputeBackend`，本阶段只实现第一种：

| id | 含义 | 本阶段 |
| --- | --- | --- |
| `local_linux` | 当前 Cursor VM / 本地 Linux | **实现** |
| `windows_pc` | Windows 个人 PC 执行 | 接口预留 |
| `cloud_single` | 云服务器单机 | 接口预留 |
| `small_cluster` | 小规模集群 | 接口预留 |
| `gpu_cluster` | 大型物理机计算卡集群 | 接口预留 |

不绑定 4090。模型推理一律经 OpenAI 兼容 `base_url`（本地 vLLM、云 API、集群网关都算这一种），且**两条路线都要实现**：

| `endpoint_kind` | 含义 | 阶段 |
| --- | --- | --- |
| `api` | 云端模型 API | 前期主用 |
| `local` | 本地 vLLM 或计算卡集群网关 | 后续，接口现在就留 |

两者在 harness 侧是同一个插件下的两条并列 route，切换是改配置而非改代码。

资源决策顺序：先在 Cursor VM 用 dummy/fake 测通平台 → 用 `doctor` 和一次（或规划中的）OSWorld 1 题估 **最小验证消耗** → 再决定真实 Qwen+dsh 跑在 Windows PC、云单机还是集群。

| 项 | 确认 |
| --- | --- |
| 现在 | Cursor VM 开发，`num_envs=1` |
| GPU | 不假设有卡；真实小 Qwen 需要 GPU 或云 API 时另选机器 |
| 调度 | 本阶段只跑本地进程；集群只留接口 |

---

## 4. 存储与产物

- **不上数据库**（不要 SQLite/PostgreSQL）。本阶段单机单题，结果就是本地文件：逐题 `result.json` 给 `report` 解析，`table1.md` 给人读。
- 轨迹、截图、录屏的保留时间做成配置项 `artifact_retention_days`，**默认 14 天**，可改。
- 评测语义 / 安全细则：等你按 bench 列表补充后再写，不在本阶段展开。
- 测试阶段磁盘：**最小按 OSWorld smoke 准备**，见 [RESOURCES.md](./RESOURCES.md) 第 6 节。不要按 8 个 bench 的镜像总和买盘。

---

## 5. 相对原默认假设的差异（很小）

| 原默认 | 本次确认 | 变化 |
| --- | --- | --- |
| 阶段 0 + OSWorld smoke | 相同 | 无 |
| screenshot-only；bash 为 flag | 禁 a11y/DOM/软件 API；guest 内打字允许；harness bash 关闭且待定 | 细化，见 6.3 |
| CLI + Markdown 表 | 仅 CLI，表可打印到终端/文件 | 无 Web |
| 单机 `num_envs=1` | Cursor VM 开发；计算后端枚举预留 | 补充 |
| Mac/Windows **bench 客户机** 预留不跑 | 不变；另增 Windows **宿主机** 接口 | 区分客户机 vs 执行机 |
| 仅 native-cua | 真实验证改为 **Qwen 小模型 + DeepSeek Harness** | 修订 |
| 约 4090 | **不绑定卡型** | 修订 |
| 内部可复现，不对齐论文 | 相同；强调小模型跑通 | 无 |
| 出网未写死 | **允许出网** | 放宽 |
| 轨迹存多久未定 | **可配置保留期，默认 14 天** | 补充 |

---

## 6. 2026-08-20 二次确认：关闭的六项待定项

### 6.1 模型接入路线

在 **harness 的配置里**声明模型端点，不在平台代码里写死。用 dsh 的 `dsh-llm-pi-ai` 插件：一个实例可持有多条 route，pi-ai 未内置的端点整份声明即可，OpenAI 兼容网关与自托管服务器都属于配置。

### 6.2 模型接入必须支持两种

`endpoint_kind=api`（云端模型 API，前期）与 `endpoint_kind=local`（本地 vLLM / 计算卡集群网关，后续）。两条 route 并列声明在同一份 cordis.yml 里，实验 YAML 选用哪条。具体 Qwen VL 的 model id 与端点地址仍是 YAML 可配字段，不写死。

**待验证**：pi-ai 自建 route 不能声明 input modalities。接 adapter 的第一步必须用一张截图做连通性测试，确认模型真收到了图；不通时退路见 [AGENTS.md](../AGENTS.md) 第 6.3 节。

### 6.3 协议边界（三个独立开关）

「禁止 bash」的含义是**禁止模型绕过图形界面、通过软件的 API / 脚本接口完成操作**，不是禁止打字。

| 开关 | 取值 | 说明 |
| --- | --- | --- |
| 观测 | 只给截图 | 禁 a11y 树、DOM、应用脚本接口 |
| 桌面动作 | 只给键鼠 | **在桌面 VM 里开终端打字属于合法键鼠操作，允许** |
| harness bash | 关闭，**是否永久禁止待后续再定** | dsh 侧直接执行命令的工具 |

`harness_bash` 以后若打开必须单列一列，不与 GUI-only 的分数混在同一列。

### 6.4 阶段 1 的单题

OSWorld-Verified `os` 域 `5ea617a3-0e86-4ba6-aab2-dac9aa2e8d57`（从回收站恢复误删的海报，evaluator `exact_match`）。纯 GUI 文件管理器操作，官方 `test_small.json` 成员。同域另一题 `5812b315`（创建 SSH 用户）本质是终端命令任务，不采用。

### 6.5 存储形态与指标口径

不上数据库（见第 4 节）。本阶段**只记 OSWorld 一个指标**：

```text
逐题分数  官方 evaluator 原样返回的 0.0–1.0，存 result.json
聚合指标  success_rate，百分数，unit="percent"
```

Table 1 渲染器仍保留双指标与 ASR 越低越好的能力，但阶段 1 不启用。

### 6.6 步数上限

`max_steps` 默认 **50**。
