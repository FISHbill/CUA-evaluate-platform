# 已确认需求（2026-08-19，2026-08-20 修订）

对照 [PLAN.md](./PLAN.md) 第 8 节默认假设：整体没有大的方向分歧。下面是本次确认后的约束，后续实现以本文为准。2026-08-20 补充：真实推理验证用 **Qwen 小模型 + DeepSeek Harness**；计算资源接口不绑定 4090。

---

## 1. 范围与成功标准

| 项 | 确认 |
| --- | --- |
| Bench 目标 | Table 1 那 **5～8 个**（OSWorld-Verified / 2.0、MyPCBench、MacAgentBench、Gym-Anything、ScienceBoard、WebArena、RedTeamCUA） |
| 当前阶段 | 开发在 **Cursor VM**。分两层「最小」：平台骨架用 fake；**真实 CUA 推理验证** 用 OSWorld-Verified **1 题** |
| 最小真实 bench | **是**：在 Table 1 的 5～8 个里，OSWorld-Verified 单任务是资源最低的真实桌面 CUA 闭环 |
| 成功标准 | **内部可复现对比**；不追求对齐论文分数。模型可能较小，能跑完循环即可 |
| 交互 | **仅 CLI**；不做 Web 看板、不做多用户 |
| 协议 | 对 OSWorld smoke：**截图 + 键鼠**（DeepSeek Harness 以 computer-use 插件接入，本阶段不把 Bash 算进协议） |
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
- **注意**：dsh 上游默认偏 coding（可含 bash/编辑器）。本阶段接到 OSWorld 时只启用 **截图 + 键鼠** 的 computer-use 能力；Bash 协议仍关闭，除非以后单独开实验列。

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

不绑定 4090。模型推理一律经 OpenAI 兼容 `base_url`（本地 vLLM、云 API、集群网关都算这一种）。

资源决策顺序：先在 Cursor VM 用 dummy/fake 测通平台 → 用 `doctor` 和一次（或规划中的）OSWorld 1 题估 **最小验证消耗** → 再决定真实 Qwen+dsh 跑在 Windows PC、云单机还是集群。

| 项 | 确认 |
| --- | --- |
| 现在 | Cursor VM 开发，`num_envs=1` |
| GPU | 不假设有卡；真实小 Qwen 需要 GPU 或云 API 时另选机器 |
| 调度 | 本阶段只跑本地进程；集群只留接口 |

---

## 4. 存储与产物

- 轨迹、截图、录屏的保留时间做成配置项，例如 `artifact_retention_days`（或按体积上限 `artifact_max_gb`）。到期删除或归档，默认值实现时再定（建议默认 14 天，可改）。
- 评测语义 / 安全细则：等你按 bench 列表补充后再写，不在本阶段展开。
- 测试阶段磁盘：**最小按 OSWorld smoke 准备**，见 [RESOURCES.md](./RESOURCES.md) 第 6 节。不要按 8 个 bench 的镜像总和买盘。

---

## 5. 相对原默认假设的差异（很小）

| 原默认 | 本次确认 | 变化 |
| --- | --- | --- |
| 阶段 0 + OSWorld smoke | 相同 | 无 |
| screenshot-only；bash 为 flag | **本阶段禁止 Bash** | 更严 |
| CLI + Markdown 表 | 仅 CLI，表可打印到终端/文件 | 无 Web |
| 单机 `num_envs=1` | Cursor VM 开发；计算后端枚举预留 | 补充 |
| Mac/Windows **bench 客户机** 预留不跑 | 不变；另增 Windows **宿主机** 接口 | 区分客户机 vs 执行机 |
| 仅 native-cua | 真实验证改为 **Qwen 小模型 + DeepSeek Harness** | 修订 |
| 约 4090 | **不绑定卡型** | 修订 |
| 内部可复现，不对齐论文 | 相同；强调小模型跑通 | 无 |
| 出网未写死 | **允许出网** | 放宽 |
| 轨迹存多久未定 | **可配置保留期** | 补充 |
