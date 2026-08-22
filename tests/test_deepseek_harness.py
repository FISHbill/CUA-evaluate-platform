"""DeepSeek Harness adapter：不走逐步 act()；mock 端点证明图片进请求。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from cua_eval.errors import ConfigError, HarnessError
from cua_eval.harness.base import StepObservation
from cua_eval.harness.deepseek import DeepSeekHarnessAdapter
from cua_eval.harness.stub import build_harness
from cua_eval.schema import (
    Experiment,
    GuestActions,
    HarnessId,
    Modality,
    ModelBackend,
    ModelSpec,
    Observation,
)
from openai_compat_mock import OpenAICompatMock, payload_has_image, tool_names_from_payload

CONFIG_DIR = Path(__file__).resolve().parents[1] / "configs" / "experiments"
OSWORLD_YAML = CONFIG_DIR / "smoke_osworld.yaml"


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


def _short_experiment(*, vision: bool, guest_shell: bool) -> Experiment:
    experiment = Experiment.from_yaml(OSWORLD_YAML)
    protocol = experiment.agent.protocol.model_copy(
        update={
            "observation": Observation.SCREENSHOT if vision else Observation.TEXT,
            "guest_shell": guest_shell,
            "guest_actions": GuestActions.MOUSE_KEYBOARD if vision else GuestActions.NONE,
        }
    )
    limits = experiment.agent.limits.model_copy(update={"task_timeout_seconds": 45})
    model = experiment.agent.model
    if not vision:
        model = model.model_copy(
            update={
                "input_modalities": [Modality.TEXT],
                "provider_route": "text-cloud",
                "name": "openai-compat-text",
            }
        )
    agent = experiment.agent.model_copy(
        update={"model": model, "protocol": protocol, "limits": limits}
    )
    return experiment.model_copy(update={"agent": agent})


def _run_dsh(tmp_path: Path, *, vision: bool, guest_shell: bool) -> OpenAICompatMock:
    experiment = _short_experiment(vision=vision, guest_shell=guest_shell)
    mock = OpenAICompatMock(vision=vision)
    mock.start()
    env = {
        **os.environ,
        "CUA_EVAL_MODEL_BASE_URL": mock.base_url,
        "CUA_EVAL_MODEL_API_KEY": "mock-key",
        "CUA_EVAL_MCP_BACKEND": "fake",
        "CUA_EVAL_MCP_GUEST_HOSTNAME": "osworld-guest",
    }
    harness = DeepSeekHarnessAdapter(experiment.agent, environ=env)
    try:
        harness.run_task("Restore the deleted poster from the trash.", work_dir=tmp_path / "dsh")
    except (HarnessError, ConfigError):
        mock.close()
        raise
    mock.close()
    return mock


def test_mock_screenshot_protocol_sends_image(tmp_path: Path) -> None:
    mock = _run_dsh(tmp_path, vision=True, guest_shell=True)
    assert mock.requests, "dsh 没有打到 mock 端点"
    assert any(payload_has_image(req) for req in mock.requests), (
        "截图协议下 mock 必须收到 image 部分，"
        "否则 modality 声明或 attachment 插件有问题"
    )


def test_mock_shell_protocol_lists_shell_and_is_text_only(tmp_path: Path) -> None:
    mock = _run_dsh(tmp_path, vision=False, guest_shell=True)
    assert mock.requests, "dsh 没有打到 mock 端点"
    names = [name for req in mock.requests for name in tool_names_from_payload(req)]
    assert any(name.endswith("__shell") or name == "shell" for name in names)
    assert not any(payload_has_image(req) for req in mock.requests)
