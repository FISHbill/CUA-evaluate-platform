"""四种失败分类：ok / task_fail / infra_error / model_error。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cua_eval.actions import ShellAction, WaitAction
from cua_eval.backends.model import DummyModel
from cua_eval.benches.base import RawResult
from cua_eval.errors import InfraError
from cua_eval.harness.base import Harness
from cua_eval.orchestrator.run import run_experiment
from cua_eval.schema import Experiment, FailureClass, ModelSpec, TrialResult

CONFIG_DIR = Path(__file__).resolve().parents[1] / "configs" / "experiments"


def _fake_experiment(tmp_path: Path) -> Experiment:
    return Experiment.from_yaml(CONFIG_DIR / "smoke_fake.yaml").model_copy(
        update={"results_dir": tmp_path / "results"}
    )


def test_dummy_success_is_ok(tmp_path: Path) -> None:
    record = run_experiment(_fake_experiment(tmp_path))
    assert len(record.trials) == 1
    trial = record.trials[0]
    assert trial.failure_class is FailureClass.OK
    assert trial.score == 1.0
    assert trial.counts_toward_score is True


def test_max_steps_without_terminate_is_task_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def build_stuck(spec: ModelSpec) -> DummyModel:
        return DummyModel(spec, actions=[WaitAction(seconds=0.0)])

    monkeypatch.setattr("cua_eval.orchestrator.run.build_model", build_stuck)
    record = run_experiment(_fake_experiment(tmp_path))
    trial = record.trials[0]
    assert trial.failure_class is FailureClass.TASK_FAIL
    assert trial.score == 0.0
    assert trial.counts_toward_score is True


def test_illegal_shell_is_model_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def build_shell(spec: ModelSpec) -> DummyModel:
        return DummyModel(spec, actions=[ShellAction(command="echo should-not-run")])

    monkeypatch.setattr("cua_eval.orchestrator.run.build_model", build_shell)
    record = run_experiment(_fake_experiment(tmp_path))
    trial = record.trials[0]
    assert trial.failure_class is FailureClass.MODEL_ERROR
    assert trial.score == 0.0
    assert "guest_shell" in (trial.error_message or "")


class _InfraBench:
    id = "fake"

    def prepare(self) -> None:
        return None

    def list_tasks(self) -> list[str]:
        return ["fake-task-0001"]

    def run_trial(self, task_id: str, agent: Harness) -> RawResult:
        del task_id, agent
        raise InfraError("kvm is not readable")

    def normalize(self, raw: RawResult) -> TrialResult:
        del raw
        raise AssertionError("infra_error 不应进入 normalize")

    def cleanup(self) -> None:
        return None


def test_infra_error_has_no_score(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("cua_eval.orchestrator.run.get_bench", lambda _exp: _InfraBench())
    record = run_experiment(_fake_experiment(tmp_path))
    trial = record.trials[0]
    assert trial.failure_class is FailureClass.INFRA_ERROR
    assert trial.score is None
    assert trial.counts_toward_score is False

    result_files = list(tmp_path.joinpath("results").rglob("result.json"))
    assert result_files
    payload = json.loads(result_files[0].read_text(encoding="utf-8"))
    assert payload["score"] is None
    assert payload["failure_class"] == "infra_error"
