"""假评测全链路：无网、无 Docker、写出结果目录。"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cua_eval.cli import app
from cua_eval.schema import Experiment, FailureClass, ModelBackend

CONFIG_DIR = Path(__file__).resolve().parents[1] / "configs" / "experiments"
FAKE_YAML = CONFIG_DIR / "smoke_fake.yaml"
OSWORLD_YAML = CONFIG_DIR / "smoke_osworld.yaml"
SCIENCEBOARD_YAML = CONFIG_DIR / "smoke_scienceboard.yaml"
MAC_YAML = CONFIG_DIR / "smoke_mac_agent_bench.yaml"


def _run_dirs(results_root: Path) -> list[Path]:
    if not results_root.exists():
        return []
    return sorted(p for p in results_root.iterdir() if p.is_dir())


def test_cli_smoke_fake_writes_result_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(app, ["run", "-c", str(FAKE_YAML)], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    assert "dummy 的分数不是模型能力" in result.output

    runs = _run_dirs(tmp_path / "results")
    assert len(runs) == 1
    run_dir = runs[0]
    run_payload = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert run_payload["experiment"]["agent"]["model"]["backend"] == "dummy"

    task_dir = run_dir / "fake" / "dummy-fixed-actions" / "fake-task-0001"
    result_payload = json.loads((task_dir / "result.json").read_text(encoding="utf-8"))
    assert result_payload["failure_class"] == FailureClass.OK.value
    assert result_payload["score"] == 1.0
    assert result_payload["model_backend"] == ModelBackend.DUMMY.value
    assert result_payload["steps"] == 2

    screenshots = list((task_dir / "screenshots").glob("*.png"))
    assert screenshots
    png = screenshots[0].read_bytes()
    assert png.startswith(b"\x89PNG\r\n\x1a\n")

    traces = [
        json.loads(line)
        for line in (task_dir / "trace.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    assert len(traces) == 2
    assert traces[0]["action"]["type"] == "wait"
    assert traces[1]["action"]["type"] == "terminate"
    assert traces[0]["screenshots_sent"] == 1
    assert traces[1]["screenshots_sent"] == 2
    assert traces[0]["screenshot_size"] == [64, 64]
    assert traces[0]["model_backend"] == "dummy"
    assert traces[-1]["screenshots_sent"] <= 4


def test_cli_help_lists_commands() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for name in ("doctor", "run", "report", "prune"):
        assert name in result.output


def test_osworld_smoke_does_not_fall_back_to_dummy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(app, ["run", "-c", str(OSWORLD_YAML)], catch_exceptions=False)
    assert result.exit_code == 1, result.output
    assert "dummy" in result.output.lower()
    assert "qcow2" in result.output or "checkout" in result.output or "OSWorld" in result.output
    assert not _run_dirs(tmp_path / "results")


def test_scienceboard_smoke_does_not_fall_back_to_dummy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(app, ["run", "-c", str(SCIENCEBOARD_YAML)], catch_exceptions=False)
    assert result.exit_code == 1, result.output
    assert "dummy" in result.output.lower()
    assert "ScienceBoard" in result.output or "VM.zip" in result.output
    assert "checkout" in result.output or "ScienceBoard" in result.output
    assert not _run_dirs(tmp_path / "results")


def test_mac_agent_bench_smoke_does_not_fall_back_to_dummy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(app, ["run", "-c", str(MAC_YAML)], catch_exceptions=False)
    assert result.exit_code == 1, result.output
    assert "dummy" in result.output.lower()
    assert "MacAgentBench" in result.output or "Fleet" in result.output
    assert "checkout" in result.output or "MacAgentBench" in result.output
    assert not _run_dirs(tmp_path / "results")


def test_smoke_fake_yaml_still_valid() -> None:
    experiment = Experiment.from_yaml(FAKE_YAML)
    assert experiment.agent.model.backend is ModelBackend.DUMMY


def test_cli_report_after_fake_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    run_result = runner.invoke(app, ["run", "-c", str(FAKE_YAML)], catch_exceptions=False)
    assert run_result.exit_code == 0, run_result.output
    run_id = next(
        line.split(maxsplit=1)[1].strip()
        for line in run_result.output.splitlines()
        if line.startswith("run_id")
    )
    report_result = runner.invoke(app, ["report", run_id], catch_exceptions=False)
    assert report_result.exit_code == 0, report_result.output
    assert "# Table 1" in report_result.output
    assert "dummy-fixed-actions" in report_result.output
    assert "dummy" in report_result.output
    table = tmp_path / "results" / run_id / "table1.md"
    assert table.is_file()
    assert "100.0" in table.read_text(encoding="utf-8")


def test_cli_prune_respects_days(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    old = tmp_path / "results" / "20260101-000000-dead"
    old.mkdir(parents=True)
    (old / "run.json").write_text("{}", encoding="utf-8")
    os.utime(old, (0, 0))
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["prune", "--results-dir", str(tmp_path / "results"), "--days", "14"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    assert not old.exists()

