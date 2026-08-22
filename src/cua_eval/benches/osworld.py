"""OSWorld-Verified adapter。阶段 0 只占位；真跑在 M5/M6。"""

from __future__ import annotations

from cua_eval.benches.base import RawResult
from cua_eval.errors import ConfigError
from cua_eval.harness.base import Harness
from cua_eval.schema import TrialResult

_MESSAGE = (
    "bench=osworld_verified adapter 由 M5 交付，且当前开发机通常不具备 Docker/KVM。"
    "阶段 0 请用 configs/experiments/smoke_fake.yaml；"
    "不要把 dummy 的分数当成模型能力。"
)


class OSWorldBench:
    id = "osworld_verified"

    def prepare(self) -> None:
        raise ConfigError(_MESSAGE)

    def list_tasks(self) -> list[str]:
        raise ConfigError(_MESSAGE)

    def run_trial(self, task_id: str, agent: Harness) -> RawResult:
        del task_id, agent
        raise ConfigError(_MESSAGE)

    def normalize(self, raw: RawResult) -> TrialResult:
        del raw
        raise ConfigError(_MESSAGE)

    def cleanup(self) -> None:
        return None
