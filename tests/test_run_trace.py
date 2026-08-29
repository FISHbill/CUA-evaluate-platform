from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from cua_eval.orchestrator.run import TracingHarness
from cua_eval.schema import Experiment
from cua_eval.store.results import TaskWriter

REPO = Path(__file__).resolve().parents[1]
CONFIG = REPO / "configs" / "experiments" / "mac_agent_bench_qwen36.yaml"


class _DshResultHarness:
    def run_task(
        self,
        instruction: str,
        *,
        work_dir: Path,
        extra_env: dict[str, str] | None = None,
    ) -> SimpleNamespace:
        del instruction, extra_env
        (work_dir / "dsh-sessions").mkdir()
        (work_dir / "dsh-sessions" / "events.jsonl").write_text('{"event":"session"}\n')
        (work_dir / "mcp-screenshots").mkdir()
        (work_dir / "mcp-screenshots" / "step-001.jpg").write_bytes(b"jpeg")
        return SimpleNamespace(
            session_id="session-1",
            finish_reason="completed",
            input_tokens=12,
            output_tokens=7,
            events=[
                {
                    "action": {"type": "click"},
                    "input_tokens": 12,
                    "output_tokens": 7,
                }
            ],
        )


def test_tracing_harness_persists_dsh_trace_and_artifacts(tmp_path: Path) -> None:
    experiment = Experiment.from_yaml(CONFIG)
    task_dir = tmp_path / "task"
    writer = TaskWriter(task_dir)
    tracing = TracingHarness(_DshResultHarness(), writer, experiment)  # type: ignore[arg-type]

    result = tracing.run_task("set an alarm", work_dir=tmp_path / "work")

    assert result.session_id == "session-1"
    assert tracing.input_tokens == 12
    assert tracing.output_tokens == 7
    assert tracing.steps == 1
    trace = [json.loads(line) for line in (task_dir / "trace.jsonl").read_text().splitlines()]
    assert trace[0]["source"] == "deepseek_harness"
    assert (task_dir / "raw" / "dsh-sessions" / "events.jsonl").is_file()
    assert (task_dir / "raw" / "dsh-run.json").is_file()
    assert (task_dir / "screenshots" / "step-001.jpg").read_bytes() == b"jpeg"
