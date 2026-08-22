"""模型客户端。

`dummy` 是平台自测用的假模型：不读截图，返回固定动作并以 `terminate` 收尾。
它的分数**不代表任何模型能力**。

`openai_compat` 是阶段 1 的真路径：任意 OpenAI 兼容端点。M1 尚未接线；
构建时直接失败，**不得**退化成 dummy。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from cua_eval.actions import Action, TerminateAction, WaitAction
from cua_eval.errors import ConfigError
from cua_eval.harness.base import Completion, StepObservation
from cua_eval.schema import ModelBackend, ModelSpec


@runtime_checkable
class ModelClient(Protocol):
    spec: ModelSpec

    def complete(self, observation: StepObservation) -> Completion: ...


class DummyModel:
    """固定动作序列。不看 `observation` 里的截图。"""

    def __init__(
        self,
        spec: ModelSpec,
        actions: list[Action] | None = None,
    ) -> None:
        if spec.backend is not ModelBackend.DUMMY:
            raise ConfigError("DummyModel 只能配 model.backend=dummy")
        self.spec = spec
        self._actions: list[Action] = actions or [
            WaitAction(seconds=0.0),
            TerminateAction(status="success"),
        ]
        self._index = 0

    def complete(self, observation: StepObservation) -> Completion:
        del observation  # dummy 不读截图，这是故意的。
        if not self._actions:
            action: Action = TerminateAction(status="fail")
        elif self._index >= len(self._actions):
            action = self._actions[-1]
        else:
            action = self._actions[self._index]
            self._index += 1
        return Completion(action=action, input_tokens=0, output_tokens=0)


def build_model(spec: ModelSpec) -> ModelClient:
    if spec.backend is ModelBackend.DUMMY:
        return DummyModel(spec)
    raise ConfigError(
        f"model.backend={spec.backend.value} 尚未接入。"
        "阶段 0 请用 dummy（configs/experiments/smoke_fake.yaml）；"
        "openai_compat 随 DeepSeek Harness 在 M4 接入。"
        "无端点时不得退化成 dummy 还宣称已经验证过模型。"
    )


__all__ = ["Completion", "DummyModel", "ModelClient", "build_model"]
