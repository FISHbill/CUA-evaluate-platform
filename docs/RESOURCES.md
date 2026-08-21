# 推理评测资源估算

本文评估 CUA 平台做 **推理验证（跑 bench，不训练）** 时的计算、存储、网络需求。数字来自各 bench 官方文档与 Qwen-CUA 论文附录的公开统计，其余为工程估算，按数量级使用即可。

评测资源分成两坨，不要混在一台「GPU 训练机」上规划：

```text
环境侧（CPU + KVM + 磁盘）     模型侧（API 或 GPU 推理）
桌面 VM / 网站沙箱 / 截图       每步吃 1～20 张图，吐动作
```

环境侧几乎不需要独立 GPU。模型侧若走云 API，评测机可以纯 CPU；若自托管 Qwen-CUA 级模型，GPU 成本会超过全部 VM。

---

## 1. 单环境定额（规划用）

| 单元 | vCPU | 内存 | 本地盘 | 备注 |
| --- | --- | --- | --- | --- |
| OSWorld 类桌面 VM（并发 1 路） | 2–8 | 8 GB | 30–64 GB | Docker 示例常见 `CPU=8, RAM=8G, DISK=64G`；AWS 客户端常用 t3.medium/large + 30 GB gp3 |
| 评测宿主机 / 控制面 | 4–16 | 16–32 GB | 50 GB+ | 官方：`<5` 路用 t3.medium，`<15` 路 t3.large，`≥15` 路 c4.8xlarge 量级 |
| WebArena 站点集群（共享，非每任务一台） | 4–8 | 16–32 GB | **1 TB 级** | 官方建议 t3a.xlarge + 1000 GB；一次性下载约 180 GB |
| ScienceBoard 宿主机 | ≥8 | **32 GB** | **>100 GB** | 官方最低推荐；核显即可，不要求独显 |
| MacAgentBench | 物理 Mac | — | — | Qwen-CUA 评测用了 **30 台 Mac mini**，Linux 无法替代 |

并发规划口算：

```text
宿主机可用内存 ≈ 16 GB 控制面 + num_envs × 8 GB
宿主机 vCPU   ≈ 4 + num_envs × 4
必须有 /dev/kvm，否则桌面 VM 会慢一个数量级
```

`num_envs=8` 的 Linux 桌面评测机：大约 **32–48 vCPU、80–96 GB RAM、KVM、500 GB SSD**。这是「能跑 OSWorld 全量」的实用规格，不是最小开发机。

---

## 2. 按规模分档

| 档 | 目标 | 计算 | 存储 | 网络 | 墙钟（数量级） |
| --- | --- | --- | --- | --- | --- |
| A. Smoke 平台 | fake + dummy，Cursor VM | 当前机即可（约 4 vCPU / 16 GB 也能做骨架） | 代码盘即可 | 无 | 分钟级 |
| A2. 真实 1 题 | OSWorld 1 题 + 小 Qwen + dsh | 需 KVM + 能跑小 VLM 的 GPU 或云 API；**不绑定 4090** | **根盘 ≥ 150 GB** | 首次下行约 15–25 GB + 模型权重 | 含下载数小时；纯跑题数十分钟 |
| B. 单 bench 全量 | OSWorld-Verified 360 题，`num_envs=8` | 上表 32–48 vCPU / 96 GB | 冷数据 100 GB + 一次 run 轨迹 5–30 GB | 评测期上行 10–40 GB 到模型 API | 约 **3–8 h**（官方称 AWS 高并行可压到 ~1 h） |
| C. Table 1 一个模型 | 8 个 bench 全量 | Linux 池 16–64 路 VM + 一台 WebArena 胖节点；Mac 另算 | 冷镜像 **0.5–2 TB**；一次 run 轨迹 **50–300 GB**（只存 JPEG）/ **0.5–2 TB**（再存录屏） | 首次下行 0.3–1 TB；评测期上行 **0.2–1 TB** 图像到 API | **数天到两周**，卡在 OSWorld 2.0 与 Mac |
| D. Table 1 四个模型 | C × 4 | 同 C，或串行复用池 | 轨迹 ×4 | API 流量 ×4 | 按机器池线性增加 |

OSWorld 2.0 是墙钟杀手：108 题、Qwen-CUA 平均 **218.9 步/题**、官方 `max_steps` 可达 500；论文里人类中位操作约 1.6 h。按 8 路并行、每题 30–60 min 估，**单模型仅这一张 bench 就要约 8–14 小时**，弱模型打满步数会更长。

---

## 3. 计算：环境 CPU vs 模型推理

### 3.1 墙钟怎么算

```text
墙钟 ≈ (任务数 / num_envs) × (reset + 平均步数 × (模型时延 + 动作执行 + sleep))
```

| Bench | 题数 | 公开步数设定 / 实测 | 8 路并行粗估 |
| --- | --- | --- | --- |
| OSWorld-Verified | 360 | 官方常 50–100 步上限；Qwen-CUA 平均 **14.2 步** | 半天内 |
| OSWorld 2.0 | 108 | 平均 **219 步**，上限 500 | 约 1 天 |
| MyPCBench | 184 | 上限 200 turn | 半天～1 天 |
| ScienceBoard | 169 | 科学软件，单步更重 | 半天～1 天 |
| WebArena | 812 | 浏览器，无完整桌面 VM | 视并行，通常短于 OSWorld 2.0 |
| Gym-Anything | 测试子集未锁 | 官方训练侧曾 400+ 并发 / 1600 CPU | 子集小时级，全量很大 |
| RedTeamCUA | 864 | OS + Web 混合 | 与 OSWorld 同量级 |
| MacAgentBench | 676 | 30 台 Mac mini 才铺得开 | 无 Mac 则跳过 |

reset 不可忽视：AWS 上每题可能重建实例（分钟级）；本地 Docker/QEMU 也要重启桌面。规划时给每题 **0.5–2 min** 的 reset，不要只按模型时延乘步数。

### 3.2 模型侧

Qwen-CUA 附录（screenshot-only）：

| | 每题平均 output tokens | 每题平均步数 |
| --- | --- | --- |
| OSWorld-Verified | 3,606 | 14.2 |
| OSWorld 2.0 | **244,626** | 218.9 |

输入主要是图，不是那几千 output token。harness 每步最多送 **20 张历史截图**：

```text
视觉 token ≈ 步数 × min(步序号, 20) × (每图 1k～2k token，视处理器)
```

粗算一次 **OSWorld 2.0 全量**：约 **10^8～10^9 量级 input token**（主要是图）+ 约 **2.6×10^7 output token**。这是 API 账单的主项；环境 CPU 费通常更便宜。

自托管：

| 模型体量 | 推理卡（数量级） |
| --- | --- |
| 7B–32B VLM | 1× 48–80 GB |
| Qwen-CUA 397B-A17B MoE | 多张 80 GB（量化后仍是多卡） |
| Qwen-CUA-Max >1T | 不适合作为评测平台默认配置 |

**建议：评测平台默认对接 API / 已有推理集群，环境机不要绑训练 GPU。**

---

## 4. 存储

### 4.1 冷数据（镜像与任务资源，下一份即可复用）

| 资产 | 量级 | 来源 |
| --- | --- | --- |
| OSWorld Ubuntu qcow2 / AMI | 数十 GB，虚拟盘常 30–64 GB | 官方 Docker/AWS |
| OSWorld 2.0 任务 assets | 需本地下载，不宜在线解析 | `download_osworld_v2_assets.py` |
| WebArena 站点镜像 | shopping 压缩约 **42 GB**，GitLab 约 **45 GB**，整包下载约 **180 GB**，盘要 **1 TB** | 官方 environment_docker |
| ScienceBoard VM | 宿主机预留 **>100 GB** | Hugging Face `ScienceBoard-Env` |
| MyPCBench gated qcow2 | 与 OSWorld 同量级或更大 | 官方 gated image |
| Gym-Anything 全量 200+ 软件 | 可达 **TB** | 必须先锁测试子集 |
| 代码与 JSON 任务表 | <1 GB | 可忽略 |

Linux 上先接通 OSWorld + WebArena + ScienceBoard：**冷存储按 1 TB 规划**。再加 Gym-Anything 全量和所有历史 run，按 2–4 TB 对象存储。

### 4.2 热数据（每次 run 的轨迹）

分辨率常见 `1920×1080`（OSWorld）或 `1280×800`（MyPCBench）。JPEG 桌面截图大约 0.2–0.5 MB，PNG 1–3 MB，录屏再高一个数量级。

只存 JPEG 截图 + JSON 动作（不存视频）：

| Run | 粗算 |
| --- | --- |
| OSWorld-Verified × 1 模型 | 2–8 GB |
| OSWorld 2.0 × 1 模型 | 10–40 GB |
| Table 1 八 bench × 1 模型 | **50–300 GB** |
| 同上 × 4 模型 | **0.2–1.2 TB** |
| 再保存屏 | 再乘 5–10 |

元数据（分数、token、错误码）很小，PostgreSQL/SQLite 即可。截图必须进对象存储，不要塞 Git。

---

## 5. 网络

分三种流量：

1. **一次性下行（镜像）**  
   Smoke：20–60 GB。接 WebArena：再加 ~180 GB。八 bench 冷启动：0.3–1 TB。需要能跑数小时的稳定外网，或内网镜像仓库。

2. **评测期上行（模型 API）** — 通常是持续带宽瓶颈  
   每步 1–20 张图。Qwen-CUA 后期每请求约 `20 × 0.3 MB ≈ 6 MB`。

   | 场景 | 上行数据量（数量级） | 8 路并发均值 |
   | --- | --- | --- |
   | OSWorld-Verified 全量 | 10–40 GB | ~50–150 Mbps 峰值 |
   | OSWorld 2.0 全量 | **100–200 GB** | 同上，时间更长 |
   | Table 1 一模型 | **0.2–1 TB** | 建议评测机 **200–500 Mbps** 出网 |

   推理服务应尽量和 VM 池同城 / 专线，避免每张图跨洋。

3. **环境出网**  
   - 当前确认：**测试评测环境可以出网**。  
   - 以后按 bench 收紧：WebArena / OSWorld 2.0 mock 站仍建议走内网；RedTeamCUA 再单独隔离。  
   - 模型 API 密钥仍不要放进桌面 VM。

控制面端口：VNC/noVNC、OSWorld server（常见 5000/8006）、WebArena 多站点端口。并发 Docker 时按官方做端口池，不要所有 VM 抢同一端口。

---

## 6. 测试阶段磁盘：最小需求（OSWorld smoke）

当前确认只跑通 **OSWorld-Verified 1 题**，不要按 8 个 bench 的镜像总和买盘。官方 Ubuntu 盘压缩包（Hugging Face `xlangai/ubuntu_osworld` 的 `Ubuntu.qcow2.zip`）约 **11.4 GB**。

> **前提**：下面的估算只在 OSWorld commit pin ≥ `091f5ef` 时成立。更早的版本每题会遗留一个约 32 GB 的匿名卷直到写满磁盘，任何盘都不够。见 [AGENTS.md](../AGENTS.md) 第 7 节。

| 用途 | 大约占用 | 说明 |
| --- | --- | --- |
| 系统与 Docker 本身 | 15–25 GB | 云主机镜像常已占一部分根盘 |
| `happysixd/osworld-docker` | 2–5 GB | QEMU/KVM 包装容器 |
| `Ubuntu.qcow2.zip` 下载 | 11.4 GB | 解压后可删 zip |
| 解压后 `Ubuntu.qcow2` | 约 12–20 GB | 稀疏文件；Docker 里 `DISK_SIZE=64G` 是虚拟容量，不是立刻占满 64 GB |
| 运行时 overlay / 日志 | 10–20 GB | 跑题时 qcow2 会涨 |
| OSWorld 代码与 Python 依赖 | 2–5 GB | |
| 1 题轨迹（JPEG，短跑） | <1 GB | 保留天数由配置控制 |
| **合计建议空闲空间** | **≥ 80 GB** | 解压期间 zip+qcow2 会短暂共存，不要只留 40 GB |
| **云主机根盘建议** | **≥ 150 GB** | 含操作系统；很多默认 40–80 GB 盘会在拉镜像时写满 |

没有 GPU 时，上述数字不变（环境侧不吃显存）。有一张 4090 时，另加模型权重盘：7B–32B 量化权重大约 **15–70 GB**，建议根盘或数据盘 **200 GB** 更从容。

各 bench 冷镜像对照（**本阶段不要一次下完**）：

| Bench | 冷数据量级 | 测试阶段 |
| --- | --- | --- |
| OSWorld-Verified | ~15–40 GB 有效占用 | **现在就要** |
| OSWorld 2.0 | 另加任务 assets | 以后 |
| WebArena | 下载约 180 GB，盘按 1 TB | 以后 |
| ScienceBoard | 宿主机预留 >100 GB | 以后 |
| MyPCBench | 与 OSWorld 同量级或更大 | 以后 |
| Gym-Anything 全量 | TB 级 | 以后，必须先锁子集 |
| Mac / Windows | 另机 | 接口预留 |

轨迹保留：做成 `artifact_retention_days`（建议默认 14，可改）。磁盘规划按「冷镜像 + 保留窗口内的 run 数 × 每 run 体积」，不要按无限历史。

---

### 6.1 机器规格（与磁盘配套）

只做 OSWorld 1 题 smoke：

- 1 台 Linux：8 vCPU、32 GB RAM、**KVM**（`/dev/kvm` 需当前用户可读）、**150 GB** 盘
- 模型：`endpoint_kind=api` 时本机无需 GPU；`endpoint_kind=local` 才需要能跑小 VLM 的卡，不绑定卡型
- 出网：允许（已确认）

参考：本项目的 Cursor 开发 VM 实测为 4 vCPU / 15 GB RAM、无 docker/qemu、`/dev/kvm` 对普通用户不可读，因此**不满足**上面这档，阶段 1 必须换执行机。阶段 0（fake + dummy）在该 VM 上可以完成。

OSWorld-Verified 360 题全量仍建议 1 TB 盘与更高并发，那是下一阶段，不是现在的最小需求。

---

## 7. 主要风险（会把预算打爆的项）

1. **按峰值步数而不是平均步数备资源**：弱模型打满 100/200/500 步，墙钟和 API 费是平均值的数倍。  
2. **20 张历史图 × 长程 bench**：OSWorld 2.0 的流量和 token 比 OSWorld-Verified 高一个数量级以上。  
3. **WebArena / 科学软件镜像**：下载和磁盘远大于「一个 Ubuntu 云桌面」。  
4. **录屏默认打开**：存储从百 GB 变 TB。  
5. **无 KVM 的云主机**：看起来 CPU 够，实际评测不可用。  
6. **环境 VM 能访问模型 API 密钥**：安全和账单双重事故。
