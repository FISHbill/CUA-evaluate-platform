"""DeepSeek Harness adapter：不走逐步 act()；mock 端点证明 pi-ai route 能打到模型。

0.1.1rc1 随 SDK 走的 jsonrpc-agent 快照包含 `dsh-mcp-client` 与
`dsh-attachment`。因此本文件只用 mock 端点验证 adapter，runtime 的实际
插件加载由目标 Linux 执行机上的 doctor / smoke 负责。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from cua_eval.errors import ConfigError
from cua_eval.harness.base import StepObservation
from cua_eval.harness.cordis import load_cordis_yaml, plugin_names
from cua_eval.harness.deepseek import DeepSeekHarnessAdapter
from cua_eval.harness.stub import build_harness
from cua_eval.schema import (
    Experiment,
    GuestActions,
    HarnessId,
    ModelBackend,
    ModelSpec,
    Observation,
    Protocol,
)
from openai_compat_mock import OpenAICompatMock, payload_has_image, tool_names_from_payload

CONFIG_DIR = Path(__file__).resolve().parents[1] / "configs" / "experiments"
OSWORLD_YAML = CONFIG_DIR / "smoke_osworld.yaml"
CORDIS = Path(__file__).resolve().parents[1] / "configs" / "dsh" / "osworld.cordis.yml"


def test_build_harness_does_not_fall_back_to_stub() -> None:
    experiment = Experiment.from_yaml(OSWORLD_YAML)
    harness = build_harness(experiment.agent)
    assert isinstance(harness, DeepSeekHarnessAdapter)
    assert experiment.agent.harness is HarnessId.DEEPSEEK_HARNESS


def test_act_is_rejected() -> None:
    experiment = Experiment.from_yaml(OSWORLD_YAML)
    harness = DeepSeekHarnessAdapter(experiment.agent)
    with pytest.raises(ConfigError, match="run_task"):
        harness.act(
            StepObservation(
                instruction="x",
                step_index=0,
                screenshot_png=None,
                screenshot_width=0,
                screenshot_height=0,
            )
        )


def test_dummy_model_rejected() -> None:
    experiment = Experiment.from_yaml(OSWORLD_YAML)
    dummy = ModelSpec(backend=ModelBackend.DUMMY, name="dummy-fixed-actions")
    protocol = experiment.agent.protocol.model_copy(update={"observation": Observation.TEXT})
    agent = experiment.agent.model_copy(update={"model": dummy, "protocol": protocol})
    with pytest.raises(ConfigError, match="dummy"):
        DeepSeekHarnessAdapter(agent)


def _runtime_compatible_cordis(path: Path, base_url: str) -> None:
    """jsonrpc-agent 快照里实际存在的最小插件组合（无 mcp / attachment）。"""
    path.write_text(
        f"""
- id: sdk-jsonrpc-server
  name: '@deepseek-ai/dsh-sdk-jsonrpc-server'
- id: agent-core
  name: '@deepseek-ai/dsh-agent-spine-demo'
  config:
    includeHarnessIdentity: false
    includeRuntimeContext: false
    workspaceContext: false
    toolBash: false
    toolJobs: false
    skills:
      enabled: false
- id: llm
  name: '@deepseek-ai/dsh-llm-pi-ai'
  config:
    providers:
      vlm-cloud:
        api: openai-completions
        baseURL: {base_url}
        apiKeyEnv: CUA_EVAL_MODEL_API_KEY
        defaultInput: [text, image]
        maxRequestImageBytes: 20971520
        models:
          - id: openai-compat-vlm
            input: [text, image]
- id: sessions
  name: '@deepseek-ai/dsh-session-persistence-jsonl'
  config:
    root: {path.parent / "sessions"}
""",
        encoding="utf-8",
    )


def test_osworld_cordis_uses_current_runtime_plugin_names() -> None:
    names = plugin_names(load_cordis_yaml(CORDIS, environ={}))
    assert "@deepseek-ai/dsh-mcp-client" in names
    assert "@deepseek-ai/dsh-attachment" in names
    assert "@deepseek-ai/dsh-attachment-local" not in names


def test_adapter_mock_text_roundtrip(tmp_path: Path) -> None:
    """adapter + pi-ai route + 本地 mock：请求打到端点。截图进请求取决于 attachment 插件。"""
    mock = OpenAICompatMock(vision=False)
    mock.start()
    try:
        cordis = tmp_path / "runtime.cordis.yml"
        _runtime_compatible_cordis(cordis, mock.base_url)
        experiment = Experiment.from_yaml(OSWORLD_YAML)
        protocol = Protocol(
            observation=Observation.TEXT,
            guest_actions=GuestActions.NONE,
            guest_shell=False,
        )
        limits = experiment.agent.limits.model_copy(update={"task_timeout_seconds": 20})
        model = experiment.agent.model.model_copy(
            update={"input_modalities": experiment.agent.model.input_modalities}
        )
        agent = experiment.agent.model_copy(
            update={"cordis_config": cordis, "protocol": protocol, "limits": limits, "model": model}
        )
        env = {
            **os.environ,
            "CUA_EVAL_MODEL_BASE_URL": mock.base_url,
            "CUA_EVAL_MODEL_API_KEY": "mock-key",
        }
        harness = DeepSeekHarnessAdapter(agent, environ=env)
        result = harness.run_task("Reply with the single word done.", work_dir=tmp_path / "dsh")
    finally:
        mock.close()
    assert mock.requests, "dsh 没有打到 mock 端点"
    assert result.finish_reason in {"completed", "stop", None} or result.final_response
    names = [name for req in mock.requests for name in tool_names_from_payload(req)]
    assert not any(name.endswith("__shell") or name == "shell" for name in names)


def test_mock_detects_image_parts() -> None:
    vision = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,xx"}}
                ],
            }
        ]
    }
    assert payload_has_image(vision)
    assert not payload_has_image({"messages": [{"role": "user", "content": "hello"}]})
