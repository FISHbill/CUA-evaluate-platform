"""把一份实验配置跑完，写成 results/<run_id>/。"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cua_eval import __version__
from cua_eval.actions import validate_against_protocol
from cua_eval.backends.compute import run_job
from cua_eval.backends.model import build_model
from cua_eval.benches.base import BenchAdapter, get_bench
from cua_eval.errors import ConfigError, InfraError, ModelError, UnsupportedBenchError
from cua_eval.harness.base import Completion, Harness, StepObservation
from cua_eval.harness.stub import build_harness
from cua_eval.schema import (
    Experiment,
    FailureClass,
    HarnessId,
    ModelBackend,
    RunRecord,
    TrialResult,
)
from cua_eval.store.results import ResultStore, StartedRun, TaskWriter


class TracingHarness:
    """包一层真实 harness：落盘截图、裁剪历史、写 trace.jsonl。"""

    def __init__(
        self,
        inner: Harness,
        writer: TaskWriter,
        experiment: Experiment,
    ) -> None:
        self._inner = inner
        self._writer = writer
        self._experiment = experiment
        self._history: list[str] = []
        self._input_tokens = 0
        self._output_tokens = 0
        self._steps = 0

    @property
    def input_tokens(self) -> int:
        return self._input_tokens

    @property
    def output_tokens(self) -> int:
        return self._output_tokens

    @property
    def steps(self) -> int:
        return self._steps

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
            "model_backend": self._experiment.agent.model.backend.value,
        }
        self._writer.append_trace(event)
        return completion

    def run_task(
        self,
        instruction: str,
        *,
        work_dir: Path,
        extra_env: dict[str, str] | None = None,
    ) -> Any:
        """dsh 整段循环。截图与动作由 desktop MCP 落在 guest 侧。"""
        run = getattr(self._inner, "run_task", None)
        if not callable(run):
            raise ConfigError("当前 harness 不支持 run_task()；deepseek_harness 才走这条路径。")
        result = run(instruction, work_dir=work_dir, extra_env=extra_env)
        events = getattr(result, "events", [])
        if not isinstance(events, list):
            events = []
        self._input_tokens = int(getattr(result, "input_tokens", 0) or 0)
        self._output_tokens = int(getattr(result, "output_tokens", 0) or 0)
        self._steps = sum(
            1
            for event in events
            if isinstance(event, dict) and ("action" in event or "tool_call" in event)
        )
        for index, event in enumerate(events):
            safe_event = json.loads(json.dumps(event, ensure_ascii=False, default=str))
            self._writer.append_trace(
                {"source": "deepseek_harness", "event_index": index, "event": safe_event}
            )
        self._copy_dsh_artifacts(Path(work_dir), result)
        return result

    def _copy_dsh_artifacts(self, work_dir: Path, result: Any) -> None:
        session_dir = work_dir / "dsh-sessions"
        if session_dir.is_dir():
            shutil.copytree(
                session_dir,
                self._writer.task_dir / "raw" / "dsh-sessions",
                dirs_exist_ok=True,
            )
        screenshot_dir = work_dir / "mcp-screenshots"
        if screenshot_dir.is_dir():
            shutil.copytree(
                screenshot_dir,
                self._writer.task_dir / "screenshots",
                dirs_exist_ok=True,
            )
        metadata = {
            "session_id": str(getattr(result, "session_id", "") or ""),
            "finish_reason": getattr(result, "finish_reason", None),
            "input_tokens": self._input_tokens,
            "output_tokens": self._output_tokens,
            "steps": self._steps,
        }
        (self._writer.task_dir / "raw" / "dsh-run.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


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
        if experiment.agent.harness is HarnessId.STUB:
            model = build_model(experiment.agent.model)
            harness = build_harness(experiment.agent, model)
        else:
            harness = build_harness(experiment.agent)
        agent = TracingHarness(harness, writer, experiment)
        raw = bench.run_trial(task_id, agent)
        if raw.raw_artifacts:
            for name, body in raw.raw_artifacts.items():
                dest = writer.task_dir / "raw" / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(body, encoding="utf-8")
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
    if experiment.agent.harness is HarnessId.STUB:
        model = build_model(experiment.agent.model)
        build_harness(experiment.agent, model)
    elif experiment.agent.harness is HarnessId.DEEPSEEK_HARNESS:
        _require_deepseek_ready(experiment)
        build_harness(experiment.agent)
    else:
        raise ConfigError(
            f"harness={experiment.agent.harness.value} 尚未接入。不要退化成 stub / dummy。"
        )
    bench = get_bench(experiment)
    bench.prepare()
    return bench


def _require_deepseek_ready(experiment: Experiment) -> None:
    if experiment.agent.model.backend is ModelBackend.DUMMY:
        raise ConfigError(
            "deepseek_harness 不能配 dummy。无端点时不要退化成 dummy 还宣称已经验证过模型。"
        )
    cordis = experiment.agent.cordis_config
    if cordis is None or not Path(cordis).is_file():
        raise ConfigError(
            f"cordis_config 不存在: {cordis}。"
            "禁止使用 dsh 零配置默认组合（那会挂上宿主机 bash）。"
            "无端点时不要退化成 dummy。"
        )


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
