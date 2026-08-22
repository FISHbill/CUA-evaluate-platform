# CUA-evaluate-platform

用于评测 **模型 + harness** 组成的 Computer-Use Agent（CUA）在多个公开 benchmark 上的表现。开发环境以 Linux 为主。

当前进度：**阶段 0（M0–M3）**。`run` / `report` / `prune` / `doctor` 均已可用。阶段 1 的 dsh / OSWorld 真跑尚未接入。

阶段 1 走**外部 OpenAI 兼容 API**，型号由 YAML `model.name` 填写，**不锁定**某一代号。本开发 VM 不足以真跑 OSWorld。

范围与技术选型均已确认，不需要重新讨论。实现以根目录 [AGENTS.md](AGENTS.md) 为准。

## 安装

```bash
uv sync
uv run cua-eval --help
```

## Smoke（阶段 0）

```bash
uv run cua-eval doctor
uv run cua-eval run -c configs/experiments/smoke_fake.yaml
uv run cua-eval report <run_id>
uv run cua-eval prune
```

`doctor` 在缺 Docker / KVM / 规格不够时退出非 0，这是预期行为，不会退化成 dummy。

dummy 的分数只说明平台链路通了，**不是**模型能力。

阶段 1 配置见 `configs/experiments/smoke_osworld.yaml`：把 `model.name`、`provider_route`、cordis.yml 的 `baseURL` 换成实际端点即可。换视觉型号只改配置，不改代码。

## 文档

- [AGENTS.md](AGENTS.md) — 给编程 agent 的开发手册
- [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md) — 已确认范围（第 6.8 节：型号不锁定）
- [docs/EXECUTION_PLAN.md](docs/EXECUTION_PLAN.md) — 执行计划：里程碑、任务判据、验收清单
- [docs/PLAN.md](docs/PLAN.md) — 架构与分期
- [docs/RESOURCES.md](docs/RESOURCES.md) — 计算 / 存储 / 网络
