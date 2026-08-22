# CUA-evaluate-platform

用于评测 **模型 + harness** 组成的 Computer-Use Agent（CUA）在多个公开 benchmark 上的表现。开发环境以 Linux 为主。

当前进度：**M0 类型层 + M1 假链路**。`cua-eval run -c configs/experiments/smoke_fake.yaml` 可在无网、无 Docker 的机器上跑完 1 题并写出 `results/`。`report` / `prune` / `doctor` 尚未实现（M2）。

阶段 1 的 OSWorld 单题验证走**外部 OpenAI 兼容 API**，型号由 YAML `model.name` 填写，**不锁定**某一代号。本开发 VM 不足以真跑 OSWorld。

范围与技术选型均已确认，不需要重新讨论。实现以根目录 [AGENTS.md](AGENTS.md) 为准。

## 安装

```bash
uv sync
uv run cua-eval --help
```

## Smoke（阶段 0）

```bash
uv run cua-eval run -c configs/experiments/smoke_fake.yaml
```

dummy 的分数只说明平台链路通了，**不是**模型能力。

阶段 1 配置见 `configs/experiments/smoke_osworld.yaml`：把 `model.name`、`provider_route`、cordis.yml 的 `baseURL` 换成实际端点即可。换视觉型号（例如从原候选的 2.5 系列换到 3 系列）只改配置，不改代码。在端点与 KVM 就绪之前不要跑这份配置。

## 文档

- [AGENTS.md](AGENTS.md) — 给编程 agent 的开发手册
- [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md) — 已确认范围（第 6.8 节：型号不锁定）
- [docs/EXECUTION_PLAN.md](docs/EXECUTION_PLAN.md) — 执行计划：里程碑、任务判据、验收清单
- [docs/PLAN.md](docs/PLAN.md) — 架构与分期
- [docs/RESOURCES.md](docs/RESOURCES.md) — 计算 / 存储 / 网络
