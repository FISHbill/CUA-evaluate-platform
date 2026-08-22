"""CI / 阶段 0 用的 stub harness：直接把观测转给 dummy 模型。"""

from __future__ import annotations

from cua_eval.backends.model import ModelClient
from cua_eval.errors import ConfigError
from cua_eval.harness.base import Completion, Harness, StepObservation
from cua_eval.schema import AgentSpec, HarnessId


class StubHarness:
    def __init__(self, model: ModelClient) -> None:
        self.model = model

    def act(self, observation: StepObservation) -> Completion:
        return self.model.complete(observation)


def build_harness(spec: AgentSpec, model: ModelClient) -> Harness:
    if spec.harness is HarnessId.STUB:
        return StubHarness(model)
    raise ConfigError(
        f"harness={spec.harness.value} 尚未接入。"
        "阶段 0 请用 stub（configs/experiments/smoke_fake.yaml）；"
        "deepseek_harness 由 M4 交付，不要退化成 stub。"
    )


__all__ = ["StubHarness", "build_harness"]
