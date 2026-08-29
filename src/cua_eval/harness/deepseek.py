"""DeepSeek Harness adapter。

dsh 的 SDK 调用集中在本文件，便于预发布版变动时收敛改动面。
`DeepSeekHarness.run()` 是**整段 agent 循环**，不是逐步 `act()`；fake bench
的逐步循环请继续用 stub。OSWorld / mock 冒烟走 `run_task()`。
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cua_eval.errors import ConfigError, HarnessError
from cua_eval.harness.base import Completion, StepObservation
from cua_eval.harness.cordis import materialize_cordis
from cua_eval.schema import AgentSpec, HarnessId, ModelBackend


@dataclass
class HarnessTaskResult:
    """把 SDK 的 RunResult 收到平台自己的类型里，调用方不必 import dsh。"""

    final_response: str
    finish_reason: str | None
    events: list[dict[str, Any]] = field(default_factory=list)
    session_id: str = ""
    input_tokens: int = 0
    output_tokens: int = 0


class DeepSeekHarnessAdapter:
    """包装官方 Python SDK。禁止退回 dsh 零配置默认组合。"""

    def __init__(self, spec: AgentSpec, *, environ: Mapping[str, str] | None = None) -> None:
        if spec.harness is not HarnessId.DEEPSEEK_HARNESS:
            raise ConfigError("DeepSeekHarnessAdapter 只能配 harness=deepseek_harness")
        if spec.model.backend is ModelBackend.DUMMY:
            raise ConfigError(
                "deepseek_harness 不能配 dummy。"
                "无端点时不要退化成 dummy 还宣称已经验证过模型。"
            )
        if spec.cordis_config is None:
            raise ConfigError("harness=deepseek_harness 必须提供 cordis_config")
        self.spec = spec
        self._environ = dict(environ) if environ is not None else dict(os.environ)
        self._session: Any = None

    def act(self, observation: StepObservation) -> Completion:
        del observation
        raise ConfigError(
            "deepseek_harness 通过 SDK run() 驱动整段 agent 循环，不支持逐步 act()。"
            "fake bench 请用 stub；真实桌面 / mock 冒烟请用 run_task()。"
        )

    def run_task(
        self,
        instruction: str,
        *,
        work_dir: Path,
        extra_env: Mapping[str, str] | None = None,
    ) -> HarnessTaskResult:
        """跑完一道题。work_dir 放 materialize 后的 cordis 与 session 日志。"""
        from deepseek_harness import DeepSeekHarness, DeepSeekHarnessConfig

        env = dict(self._environ)
        if extra_env:
            env.update(extra_env)
        work_dir = Path(work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)
        session_root = work_dir / "dsh-sessions"
        session_root.mkdir(parents=True, exist_ok=True)
        env["DSH_SESSION_ROOT"] = str(session_root)
        env["CUA_EVAL_MCP_SCREENSHOT_DIR"] = str(work_dir / "mcp-screenshots")
        cordis_raw = self.spec.cordis_config
        if cordis_raw is None:
            raise ConfigError("harness=deepseek_harness 必须提供 cordis_config")
        cordis_src = Path(cordis_raw)
        if not cordis_src.is_file():
            raise ConfigError(
                f"cordis_config 不存在: {cordis_src}。"
                "禁止使用 dsh 零配置默认组合（那会挂上宿主机 bash）。"
            )
        cordis_path = materialize_cordis(
            cordis_src,
            work_dir / "osworld.cordis.yml",
            protocol=self.spec.protocol,
            max_screenshot_history=self.spec.limits.max_screenshot_history,
            environ=env,
        )
        route = self.spec.model.provider_route
        if not route:
            raise ConfigError("openai_compat 必须提供 provider_route")
        config = DeepSeekHarnessConfig(
            provider=route,
            model=self.spec.model.name,
            cordis=str(cordis_path.resolve()),
            cwd=str(work_dir.resolve()),
            session_root=str(session_root.resolve()),
            env={k: str(v) for k, v in env.items()},
            request_timeout_seconds=float(self.spec.limits.task_timeout_seconds),
            shutdown_timeout_seconds=5.0,
        )
        try:
            with DeepSeekHarness(config) as harness:
                result = harness.run(instruction)
        except ConfigError:
            raise
        except Exception as exc:
            raise HarnessError(f"deepseek_harness 运行失败: {exc}") from exc
        events = [dict(event) for event in result.events]
        input_tokens = sum(
            int(event.get("input_tokens") or 0)
            for event in events
            if isinstance(event.get("input_tokens"), int | float)
        )
        output_tokens = sum(
            int(event.get("output_tokens") or 0)
            for event in events
            if isinstance(event.get("output_tokens"), int | float)
        )
        return HarnessTaskResult(
            final_response=result.final_response,
            finish_reason=result.finish_reason,
            events=events,
            session_id=result.session_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )


__all__ = ["DeepSeekHarnessAdapter", "HarnessTaskResult"]
