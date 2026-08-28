# 跳板机资源与仓库进度（2026-08-28）

把本轮对话里已经核实的事实收成一份备忘：**跳板机上现在有什么、还缺什么配置、代码做到哪**。给后续在执行机上继续阶段 1 / 新 bench 真跑用。

范围以对话中的实测为准，不替代 [REQUIREMENTS.md](./REQUIREMENTS.md) / [AGENTS.md](../AGENTS.md)。数字规格的一般规划仍见 [RESOURCES.md](./RESOURCES.md)。

**不要写进本文件或 git 的内容**：AWS Access Key / Secret、SSH 私钥、lucwei 管理员 `X-Access-Label`、VNC 密码、模型 API key。这些只放在执行机环境变量或本机凭据里。

---

## 1. 角色不要混

| 角色 | 是什么 | 本轮结论 |
| --- | --- | --- |
| Cursor 开发 VM | 写代码、跑 `pytest` / fake smoke | 4 vCPU / 15 GB，无 Docker/QEMU，`/dev/kvm` 普通用户不可读。只做阶段 0 与 adapter，不真跑桌面题。 |
| 跳板机 `sn6-group_holden` | 评测**控制机**：跑 `cua-eval`、dsh、调远程沙箱 | 规格够当控制面；**没有 KVM**，不能当 OSWorld / ScienceBoard 的桌面宿主机。 |
| lucwei 裸金属 Mac 池 | Mac 桌面客户机 | pack 只有 **macOS** 和 **WAA**，没有 Ubuntu / OSWorld / ScienceBoard。 |
| 模型端点 | OpenAI 兼容 HTTP | 仍未在配置里填好；无端点时 `doctor` 必须失败，不得退化 dummy。 |

不能把 OSWorld Ubuntu 题搬到 Mac 沙箱上打官方分。OSWorld / ScienceBoard / MacAgentBench 是三列，协议或客户机不同不得进 Table 1 同一列。

---

## 2. 跳板机上已有可用资源

登录方式见操作方自己的 SSM 手册（本仓库不存密钥）。只考虑**第一台**。

### 2.1 `sn6-group_holden`（评测控制机）

| 项 | 已核实的值 |
| --- | --- |
| 别名 | `sn6-group_holden` |
| Instance | `i-0a6ee61ecef51948f` |
| 账号 / IAM | `085995317762` / `holdenlin` |
| Region | `ap-southeast-3` |
| 登录 | AWS SSM SSH（非公网直连）；登录后用户 `holdenlin@ip-10-255-11-183` |
| 机型 | `m5d.16xlarge`：64 vCPU / 249 GiB |
| 根盘 | 约 447 G，对话时剩余约 294 G |
| Docker | 有，用户在 docker 组 |
| `/dev/kvm` | **没有**（Nitro 嵌套虚拟化 `Operation not supported`） |
| 出口 IP | `16.79.104.26`（已进 lucwei 白名单） |

跳板机是控制机，**不**在这台机上跑 lucwei 沙箱本身，也**不**提供 KVM。

本机 Docker 里当时能看到的是 Gym-Anything **Moodle**（`ga/moodle_env` + 运行中 checkpoint），以及 pptbench / 训练 / SWE-bench 一类镜像，**不能**拿来当 OSWorld、ScienceBoard 或 WebArena 桌面。

Lustre 上有 `gym-anything-bench`、`OSWorld-V2`、`GuiAgent` 等源码，供对照，不是本平台已经 checkout 好的 pin。

### 2.2 第二台（本轮不用）

| 项 | 值 |
| --- | --- |
| 别名 | `sn6-group_holden_2_share` |
| Instance | `i-02c471dec1502a1f3` |
| 结论 | holdenlin IAM **不能** SSM；上面有 WebArena 镜像。后续只看第一台。 |

### 2.3 lucwei 裸金属 Mac 池（holdenlin 自己的 1 台）

控制面：`http://115.159.49.74:5999`（OpenAPI 标题 Bare-metal macOS Fleet Manager 3.1.0）。鉴权头：`X-Access-Label: <pool_id>`。池 id **不是口令**；乱写会 `unknown access label`。管理员 label 在 lustre 手册里，**不要进 git**。

holdenlin 的池：`holdenlin-dev`（用该 label **只能看到自己的 1 台**；admin 接口 403；不是别人的 `lucwei-cua-gym` / `yannhua-*` / `waa-local-smoke`）。

| 项 | 已核实的值 |
| --- | --- |
| 显示名 | `holdenlin-dev/01` |
| UUID | `7cdb12cc-6056-4025-a4a4-7e6683bbb181` |
| 宿主机 | `bm-remote-07`，`kvm: true`，4C/16G |
| pack | `v8-codebuddy-proxy8888`（CodeBuddy / MacAgentBench 镜像，docker 名 `mac_agent_bench_07`） |
| SSH | `124.223.88.154:5107`（OpenSSH_10.2） |
| VNC | `:5007`（RFB 003.008） |
| HTTP | `:5207`（guest 轨迹页） |
| 截图 | `GET /api/v2/vms/<uuid>/screenshot` + `X-Access-Label: holdenlin-dev` → 1920×1080 PNG，**已通** |

注意：这块盘是 **CodeBuddy pack**，不一定带齐官方 MacAgentBench 题的 setup。adapter 仍应调官方 evaluator；环境不对记 `infra_error`，不要记成模型 0 分。

pack **只有 macOS 和 WAA**，没有 Ubuntu / OSWorld / ScienceBoard。

### 2.4 已经拍板、不要推翻的产品结论

1. 不能在 Mac 沙箱上跑 OSWorld Ubuntu 官方分。
2. 截图 CUA 必须用声明了 `image` 的视觉型号；给纯文本型号硬写 image 会 400 并毒化会话。
3. dsh 缺的是**控制机 runtime 插件**（`dsh-mcp-client` / `dsh-attachment-local`），不是沙箱里少装 SDK。dsh 跑在跳板机；沙箱只提供桌面，不要装密钥。
4. 自备 cordis **禁止**挂 `dsh-bash-local` / `dsh-subprocess-local` / `dsh-fs-local`。guest shell 走本仓库 MCP。
5. pin：`deepseek-harness-sdk==0.1.0rc7`。该 runtime-bin **没有** mcp-client / attachment-local 时，`doctor` 必须说清楚并失败。

---

## 3. 还需要配置 / 准备的部分

按 bench 拆。都未在跳板机上完成「能打官方分的真跑」。

### 3.1 公共（所有真跑）

| 项 | 状态 | 要做什么 |
| --- | --- | --- |
| 把本仓库同步到跳板机 | 未在本轮做 | clone / checkout 当前开发分支，`uv sync` |
| 模型端点 | **未配置** | 填 `CUA_EVAL_MODEL_BASE_URL`（或 local 的 `CUA_EVAL_MODEL_LOCAL_BASE_URL`）和 `CUA_EVAL_MODEL_API_KEY`；cordis 里现在是 `<unconfigured>` |
| 视觉型号 | YAML 可改，未锁定 | `model.name` + cordis route 的 `input: [text, image]`；接图连通性要单独测，不要凭型号名假设 |
| dsh runtime 插件 | 控制机上要核 | `cua-eval doctor -c …` 必须能加载 `dsh-attachment-local` 与 `dsh-mcp-client`；缺则换带完整插件集的 runtime，不要假装跑过 |
| 密钥落点 | 硬约束 | 只进控制机环境变量，不进 git / YAML / 桌面 VM |

### 3.2 OSWorld-Verified（阶段 1 原计划 1 题）

烟测：`5ea617a3-0e86-4ba6-aab2-dac9aa2e8d57`，配置 `configs/experiments/smoke_osworld.yaml`。

| 项 | 状态 | 要做什么 |
| --- | --- | --- |
| 执行机 KVM | 跳板机 **没有** | 另找带 `/dev/kvm` 的 Linux（[RESOURCES.md](./RESOURCES.md) 6.1：8 vCPU / 32 GB / ≥150 GB） |
| `third_party/OSWorld` pin ≥ `091f5ef` | 代码要求有，跳板机未 checkout | 手动 clone，不要浮动 main |
| `Ubuntu.qcow2` | 禁止 adapter 下载 | 放到 `docker_vm_data/` |
| 镜像 `happysixd/osworld-docker` | CI 禁止 pull | 在执行机上手动 `docker pull` |
| lucwei | **无 Ubuntu pack** | 不能用 Mac 池冒充 OSWorld |

### 3.3 MacAgentBench

烟测：`clock/1_1`，配置 `configs/experiments/smoke_mac_agent_bench.yaml`。控制面**不**要求本机 KVM。

| 项 | 状态 | 要做什么 |
| --- | --- | --- |
| Fleet 可达、能拉图 | 对话中已通 | 在跳板机 export 下面这组变量后跑 `doctor` |
| `third_party/MacAgentBench` pin `65632d1…` | 代码要求有 | 手动 clone |
| SSH 到 guest（键鼠 / guest shell / 官方 evaluator） | 端口已知，登录未写入平台配置 | 配 `CUA_EVAL_MAC_SSH_*`；密码只通过 `CUA_EVAL_MAC_SSH_PASSWORD_ENV` 指向的环境变量 |
| 题面 setup 是否与官方 HDD 一致 | **存疑** | CodeBuddy pack 可能缺官方 `init_task`；失败记 `infra_error` |
| 本机 Docker-OSX / HDD | 不走这条 | adapter 禁止下载 HDD |

建议在跳板机（值按第 2.3 节，不要提交到 git）：

```bash
export CUA_EVAL_MAC_FLEET_URL='http://115.159.49.74:5999'
export CUA_EVAL_MAC_POOL='holdenlin-dev'
export CUA_EVAL_MAC_VM_UUID='7cdb12cc-6056-4025-a4a4-7e6683bbb181'
export CUA_EVAL_MAC_SSH_HOST='124.223.88.154'
export CUA_EVAL_MAC_SSH_PORT='5107'
export CUA_EVAL_MAC_SSH_USER='<guest 用户>'
export CUA_EVAL_MAC_SSH_KEY='<私钥路径，可选>'
# 或：export CUA_EVAL_MAC_SSH_PASSWORD_ENV='<存放口令的环境变量名>'
```

### 3.4 ScienceBoard

烟测：`KAlgebra/A-01`，配置 `configs/experiments/smoke_scienceboard.yaml`。

| 项 | 状态 | 要做什么 |
| --- | --- | --- |
| VMware / `vmrun` | 跳板机 **没有** | 需要能跑官方 VMware DesktopEnv 的机器（官方建议 ≥8 核 / 32 GB / **>100 GB**） |
| lucwei ScienceBoard pack | **没有** | 不能用 Mac 池 |
| `third_party/ScienceBoard` pin `c8d5010…` | 代码要求有 | 手动 clone |
| 环境盘 `.vmx` 或已下载的 `VM.zip` | **没有**；adapter **禁止下载** | 设 `CUA_EVAL_SCIENCEBOARD_VM_PATH` 或 `VM_PATH` |

### 3.5 Cursor 开发 VM 上不必再配

`uv run pytest`（无网、无 Docker）必须绿。不要在这台机上下 qcow2 / VM.zip / Mac HDD，也不要装模型密钥后假装「已验证」。

---

## 4. 目前 repo 开发进度

日期：2026-08-28。分支：`cursor/scienceboard-macagentbench-6249`（基于 M1–M5 管线）。PR：[#7](https://github.com/fishbill/cua-evaluate-platform/pull/7)。`main` 仍停在 M0（`a95461d`）。

### 4.1 里程碑

| 里程碑 | 内容 | 状态 |
| --- | --- | --- |
| M0 | 包骨架、schema、异常 | **完成**（已在 `main`） |
| M1 | fake + dummy 端到端 | **完成**（本分支 / PR #6 管线） |
| M2 | `report` / `prune` | **完成** |
| M3 | pytest + CI（禁止拉镜像） | **完成** |
| M4 | dsh adapter、desktop MCP、自备 cordis、`doctor` 探插件 | **完成代码**；真 runtime 插件集要在执行机上再核 |
| M5 | OSWorld adapter（官方 evaluator，不下载 qcow2） | **完成代码，未真跑** |
| ScienceBoard / MacAgentBench | 两个新 bench adapter，与 OSWorld 分列 | **完成代码，未真跑**（本轮） |
| M6 | 换机：OSWorld 1 题真实推理验证 | **未开始** |

阶段 0 完成标准（fake / dummy / Table 1 / `local_linux`）在 Cursor VM 上已满足。阶段 1 完成标准（OSWorld 1 题 + 视觉端点 + dsh）**还没有**任何一次真跑。

### 4.2 代码里已经有的东西

- CLI：`cua-eval doctor|run|report|prune`
- Bench：`fake`、`osworld_verified`、`scienceboard`、`mac_agent_bench`；通用 `macos` / `windows` 仍 `UnsupportedBenchError`
- 实验 YAML：`smoke_fake.yaml`、`smoke_osworld.yaml`、`smoke_scienceboard.yaml`、`smoke_mac_agent_bench.yaml`
- Mac guest：`lucwei_mac`（Fleet 截图 + SSH 键鼠 / guest shell）
- ScienceBoard：包装 `VMTask.eval()`，**不**调用官方 `Tester()`
- 无网测试：对话结束时 `pytest` 185 passed / 3 skipped（`osworld` / `scienceboard` / `mac_agent_bench` live 默认 skip）

### 4.3 明确还没做的

- 跳板机或任何执行机上的 M6 真跑
- 官方 OSWorld / ScienceBoard / MacAgentBench 仓库的 pin checkout（`third_party/` 在 `.gitignore`）
- 填模型 `baseURL` 与密钥
- 换带 mcp-client / attachment-local 的 dsh runtime（若当前 rc7 bin 仍缺）
- 为 ScienceBoard 准备 VMware 盘与 `vmrun` 机器
- 确认 lucwei CodeBuddy 盘能否通过 `clock/1_1` 的官方 evaluator
- WebArena / Gym-Anything / 其它 Table 1 bench

### 4.4 建议的下一步顺序

1. 跳板机：装本仓库、配模型环境变量、跑 `cua-eval doctor -c configs/experiments/smoke_mac_agent_bench.yaml`（不依赖 KVM）。
2. 若 doctor 只卡 dsh 插件：先解决控制机 runtime，再跑 Mac 单题。
3. OSWorld：另选带 KVM 的 Linux，不要指望跳板机或 lucwei Mac 池。
4. ScienceBoard：另选 VMware 机器；lucwei 当前没有对应 pack。

---

## 5. 相关路径

| 文件 | 用途 |
| --- | --- |
| [AGENTS.md](../AGENTS.md) | 实现硬约束 |
| [EXECUTION_PLAN.md](./EXECUTION_PLAN.md) | 里程碑判据 |
| [RESOURCES.md](./RESOURCES.md) | 规格与磁盘 |
| `configs/experiments/smoke_*.yaml` | 四条烟测实验 |
| `configs/dsh/osworld.cordis.yml` | 自备 dsh（Mac / ScienceBoard 复用） |
