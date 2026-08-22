"""把一份实验配置跑完，写成 results/<run_id>/。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from cua_eval import __version__
from cua_eval.actions import validate_against_protocol
from cua_eval.backends.compute import run_job
from cua_eval.backends.model import ModelClient, build_model
from cua_eval.benches.base import BenchAdapter, get_bench
from cua_eval.errors import ConfigError, InfraError, ModelError, UnsupportedBenchError
from cua_eval.harness.base import Completion, Harness, StepObservation
from cua_eval.harness.stub import build_harness
from cua_eval.schema import Experiment, FailureClass, RunRecord, TrialResult
from cua_eval.store.results import ResultStore, StartedRun, TaskWriter


class TracingHarness:
    """包一层真实 harness：落盘截图、裁剪历史、写 trace.jsonl。"""

    def __init__(
        self,
        inner: Harness,
        writer: TaskWriter,
        experiment: Experiment,
        model: ModelClient,
    ) -> None:
        self._inner = inner
        self._writer = writer
        self._experiment = experiment
        self._model = model
        self._history: list[str] = []

    def act(self, observation: StepObservation) -> Completion:
        screenshot_rel: str | None = None
        if observation.screenshot_png is not None:
            path = self._writer.save_screenshot(observation.step_index, observation.screenshot_png)
            screenshot_rel = path.relative_to(self._writer.task_dir).as_posix()
            self._history.append(screenshot_rel)
            max_hist = self._experiment.agent.limits.max_screenshot_history
            if len(self._history) > max_hist:
                self._history = self._history[-max_hist:]
        screenshots_sent = len(self._history) if screenshot_rel is not None else 0
        completion = self._inner.act(observation)
        validate_against_protocol(completion.action, self._experiment.agent.protocol)
        event: dict[str, Any] = {
            "step": observation.step_index,
            "observation_ref": screenshot_rel,
            "screenshot_size": [observation.screenshot_width, observation.screenshot_height],
            "screenshots_sent": screenshots_sent,
            "action": completion.action.model_dump(mode="json"),
            "input_tokens": completion.input_tokens,
            "output_tokens": completion.output_tokens,
            "model_backend": self._model.spec.backend.value,
        }
        self._writer.append_trace(event)
        return completion


def _trial_from_infra(task_id: str, exc: InfraError) -> TrialResult:
    return TrialResult(
        task_id=task_id,
        failure_class=FailureClass.INFRA_ERROR,
        score=None,
        error_message=str(exc),
    )


def _trial_from_model(task_id: str, exc: ModelError) -> TrialResult:
    return TrialResult(
        task_id=task_id,
        failure_class=FailureClass.MODEL_ERROR,
        score=0.0,
        error_message=str(exc),
    )


def _run_one_task(
    *,
    experiment: Experiment,
    bench: BenchAdapter,
    started: StartedRun,
    task_id: str,
) -> TrialResult:
    writer = started.task_writer(experiment.bench.value, experiment.agent.model.name, task_id)
    extra = {
        "model_backend": experiment.agent.model.backend.value,
        "model_name": experiment.agent.model.name,
        "harness": experiment.agent.harness.value,
        "endpoint_kind": (
            experiment.agent.model.endpoint_kind.value
            if experiment.agent.model.endpoint_kind
            else experiment.agent.model.backend.value
        ),
    }
    try:
        model = build_model(experiment.agent.model)
        harness = build_harness(experiment.agent, model)
        agent = TracingHarness(harness, writer, experiment, model)
        raw = bench.run_trial(task_id, agent)
        trial = bench.normalize(raw)
    except ModelError as exc:
        trial = _trial_from_model(task_id, exc)
    except InfraError as exc:
        trial = _trial_from_infra(task_id, exc)
    except (ConfigError, UnsupportedBenchError):
        raise
    except Exception as exc:
        trial = _trial_from_infra(task_id, InfraError(f"unexpected error: {exc}"))
    writer.write_result(trial, extra=extra)
    return trial


def _preflight(experiment: Experiment) -> BenchAdapter:
    """未实现的模型 / harness / bench 必须在写 results/ 之前失败，且不得退化成 dummy。"""
    model = build_model(experiment.agent.model)
    build_harness(experiment.agent, model)
    bench = get_bench(experiment)
    bench.prepare()
    return bench


def _run_in_process(experiment: Experiment) -> RunRecord:
    bench = _preflight(experiment)
    store = ResultStore(experiment.results_dir)
    started = store.start_run(experiment)
    try:
        trials = [
            _run_one_task(
                experiment=experiment,
                bench=bench,
                started=started,
                task_id=task_id,
            )
            for task_id in experiment.task_ids
        ]
    finally:
        bench.cleanup()

    record = RunRecord(
        run_id=started.run_id,
        created_at=datetime.now(UTC),
        experiment=experiment,
        trials=trials,
        cua_eval_version=__version__,
    )
    started.write_run_record(record)
    return record


def run_experiment(experiment: Experiment) -> RunRecord:
    """入口：按 `compute_backend` 调度。未实现的后端在进循环之前就失败。"""
    return run_job(experiment.compute_backend, lambda: _run_in_process(experiment))
