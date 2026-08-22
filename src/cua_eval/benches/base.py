"""Bench adapter 协议与原始结果。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from cua_eval.harness.base import Harness
from cua_eval.schema import Experiment, FailureClass, TrialResult


@dataclass
class RawResult:
    """一次 trial 的原始结果。adapter 的 `normalize` 把它收成 `TrialResult`。"""

    task_id: str
    steps: int
    wall_time_seconds: float
    terminated: bool
    terminate_status: str | None
    evaluator_score: float | None
    failure_class: FailureClass
    error_message: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0


@runtime_checkable
class BenchAdapter(Protocol):
    id: str

    def prepare(self) -> None: ...

    def list_tasks(self) -> list[str]: ...

    def run_trial(self, task_id: str, agent: Harness) -> RawResult: ...

    def normalize(self, raw: RawResult) -> TrialResult: ...

    def cleanup(self) -> None: ...


def get_bench(experiment: Experiment) -> BenchAdapter:
    from cua_eval.benches.fake import FakeBench
    from cua_eval.benches.macos import MacOSBench
    from cua_eval.benches.osworld import OSWorldBench
    from cua_eval.benches.windows import WindowsBench
    from cua_eval.schema import BenchId

    match experiment.bench:
        case BenchId.FAKE:
            return FakeBench(experiment)
        case BenchId.OSWORLD_VERIFIED:
            return OSWorldBench()
        case BenchId.MACOS:
            return MacOSBench()
        case BenchId.WINDOWS:
            return WindowsBench()
