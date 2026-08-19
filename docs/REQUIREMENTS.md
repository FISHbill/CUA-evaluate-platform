# 已确认需求（2026-08-19）

对照 [PLAN.md](./PLAN.md) 第 8 节默认假设：整体没有大的方向分歧。下面是本次确认后的约束，后续实现以本文为准。

---

## 1. 范围与成功标准

| 项 | 确认 |
| --- | --- |
| Bench 目标 | Table 1 那 **5～8 个**（OSWorld-Verified / 2.0、MyPCBench、MacAgentBench、Gym-Anything、ScienceBoard、WebArena、RedTeamCUA） |
| 当前阶段 | **只跑通**：选资源最小的一个真实 bench，做 1 题 smoke |
| 最小真实 bench | **OSWorld-Verified 单任务**（见下） |
| 成功标准 | **内部可复现对比**；不追求对齐论文分数。模型可能较小，能跑完循环即可 |
| 交互 | **仅 CLI**；不做 Web 看板、不做多用户 |
| 协议 | **只看截图 + 键鼠**；不做厂商 Computer Use API，不做 Bash |
| Mac / Windows | 接口预留，本阶段不跑 |
| 出网 | 评测环境 **允许出网**（后续按 bench 再收紧安全语义） |

### 为什么 smoke 选 OSWorld-Verified，而不是 WebArena

WebArena 看起来「只是浏览器」，但官方站点镜像下载约 **180 GB**、盘要按 **1 TB** 准备。OSWorld-Verified 只要一份 Ubuntu 桌面盘：压缩包约 **11.4 GB**，就能做完整的「截图 → 模型 → 键鼠 → 官方打分」闭环。OSWorld 2.0、ScienceBoard、MyPCBench、RedTeamCUA 都更重。

骨架阶段仍先用 **fake bench**（不启动 VM）保证 CI；接到真实环境时用 OSWorld 一题。

---

## 2. 「模型端点」和「harness」是什么

不是「平台里内置四个模型名」，而是两层可替换组件：

```text
截图 ──► Harness（循环：拼历史、调模型、把输出变成 click/type）
              │
              ▼
         模型端点（一个 HTTP 地址，或本地 GPU 上的推理进程）
```

- **Harness**：agent 运行时。本阶段只有一种：`native-cua`（截图 in，键鼠 out）。以后若加 Bash / OpenClaw，再加第二种，和这次的分数分开记。
- **模型端点**：harness 把图和文字 POST 到哪里。可以是：
  - 本机 CPU 上的 **dummy**（固定空动作，用来测环境，不测模型）
  - 本机 **4090 上的 vLLM / SGLang**（OpenAI 兼容 `http://127.0.0.1:8000/v1`）
  - 租赁云上的同一类地址
  - 以后集群上的网关（同一套 OpenAI 兼容接口）

所以「接端点」= 在 YAML 里写 `base_url` + `model` 名，**不是**写死 Qwen-CUA / GPT-5.5。小模型、4090、纯 CPU dummy 都走同一接口。

---

## 3. 运行形态

| 项 | 确认 |
| --- | --- |
| 现在 | **一台机器**，`num_envs=1`，跑通优先 |
| 计算卡 | 现在可能 **没有 GPU（纯 CPU + dummy/API）**；跑通阶段可能租云主机 + **约 4090 级** 一张卡。具体规格另议 |
| 以后 | 可能上 **计算卡集群**。现在就要留 `ModelBackend` 接口（OpenAI 兼容 HTTP），不要把推理写死在评测循环里 |
| 环境执行 | 现在单机 Docker/QEMU；调度接口预留「本地进程 / 以后 K8s Job」，本阶段只实现本地 |

4090（24 GB）够跑量化后的中小 VLM（大约 7B–32B 档），不够跑 Qwen-CUA 397B。这与「小模型、先跑通」一致。

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
| 单机 `num_envs=1` | 相同，并预留集群模型接口 | 补充 |
| Mac/Windows 预留不跑 | 相同 | 无 |
| 内部可复现，不对齐论文 | 相同；强调小模型跑通 | 无 |
| 出网未写死 | **允许出网** | 放宽 |
| 轨迹存多久未定 | **可配置保留期** | 补充 |
