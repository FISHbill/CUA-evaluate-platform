"""ScienceBoard adapter：无网、不下载 VM.zip。真跑由 skip 门禁挡住。"""

from __future__ import annotations

import json
import subprocess
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from cua_eval.actions import TerminateAction
from cua_eval.benches.base import RawResult, get_bench
from cua_eval.benches.fake import solid_color_png
from cua_eval.benches.scienceboard import ScienceBoardBench, collect_preflight
from cua_eval.benches.scienceboard_guest import ScienceBoardGuest
from cua_eval.errors import ConfigError
from cua_eval.harness.base import Completion, StepObservation
from cua_eval.schema import BenchId, Experiment, FailureClass

CONFIG_DIR = Path(__file__).resolve().parents[1] / "configs" / "experiments"
SCI_YAML = CONFIG_DIR / "smoke_scienceboard.yaml"
SMOKE_TASK = "KAlgebra/A-01"


class _TerminatingHarness:
    def act(self, observation: StepObservation) -> Completion:
        del observation
        return Completion(action=TerminateAction(status="success"))


class _RecordingHarness:
    def __init__(self) -> None:
        self.extra_env: dict[str, str] | None = None
        self.instruction = ""

    def act(self, observation: StepObservation) -> Completion:
        del observation
        raise AssertionError("dsh 路径不应走逐步 act()")

    def run_task(
        self,
        instruction: str,
        *,
        work_dir: Path,
        extra_env: dict[str, str] | None = None,
    ) -> str:
        del work_dir
        self.instruction = instruction
        self.extra_env = extra_env
        return "done"


class _FakeManager:
    def __init__(self, png: bytes) -> None:
        self.png = png
        self.python: list[str] = []
        self.bash: list[str] = []
        self.vm_ip = "10.0.0.3"
        self.closed = False

    def screenshot(self) -> Image.Image:
        return Image.open(BytesIO(self.png)).convert("RGB")

    def __call__(self, code: str) -> None:
        self.python.append(code)

    def _execute(self, command: str | list[str], shell: bool = False) -> dict[str, Any]:
        del shell
        text = command if isinstance(command, str) else " ".join(command)
        self.bash.append(text)
        return {"output": "sci-guest\n", "error": "", "returncode": 0}

    def __exit__(self, *_args: object) -> None:
        self.closed = True


class _FakeTask:
    def __init__(self, manager: _FakeManager) -> None:
        self.manager = manager
        self.init_called = False
        self.eval_called = False

    def init(self) -> bool:
        self.init_called = True
        return True

    def eval(self) -> bool:
        self.eval_called = True
        return True


def _write_task_json(root: Path) -> None:
    path = root / "tasks" / "VM" / "KAlgebra" / "A-01.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "type": "KAlgebra",
                "sort": "VM",
                "steps": 5,
                "instruction": "Calculate the conjugate of (1 + i).",
                "version": "0.1",
                "initialize": [],
                "evaluate": [{"type": "var", "key": "p", "value": "1+-i"}],
            }
        ),
        encoding="utf-8",
    )


def _init_git_repo(path: Path) -> str:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "t@example.com"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True, capture_output=True)
    (path / "README").write_text("scienceboard pin fixture\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=path, check=True, capture_output=True, text=True
    )
    return head.stdout.strip()


def test_get_bench_passes_experiment() -> None:
    experiment = Experiment.from_yaml(SCI_YAML)
    bench = get_bench(experiment)
    assert isinstance(bench, ScienceBoardBench)
    assert bench.id == "scienceboard"
    assert experiment.bench is BenchId.SCIENCEBOARD
    assert experiment.task_ids == [SMOKE_TASK]


def test_prepare_fails_without_checkout_and_does_not_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("ScienceBoard adapter 禁止下载")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    experiment = Experiment.from_yaml(SCI_YAML)
    missing = tmp_path / "no-such-sci"
    with pytest.raises(ConfigError, match="不会退化成 dummy") as exc:
        ScienceBoardBench(experiment, root=missing, environ={}).prepare()
    assert "VM.zip" in str(exc.value) or "clone" in str(exc.value) or "vmrun" in str(exc.value)


def test_prepare_refuses_download_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CUA_EVAL_SCIENCEBOARD_ALLOW_DOWNLOAD", "1")
    experiment = Experiment.from_yaml(SCI_YAML)
    with pytest.raises(ConfigError, match="禁止自动下载"):
        ScienceBoardBench(experiment, root=tmp_path).prepare()


def test_preflight_names_missing_vm_and_does_not_require_qcow2(tmp_path: Path) -> None:
    root = tmp_path / "ScienceBoard"
    _init_git_repo(root)
    checks = {item.name: item for item in collect_preflight(root, "deadbeef", environ={})}
    assert not checks["scienceboard_vm"].ok
    assert "VM.zip" in checks["scienceboard_vm"].detail
    assert "qcow2" not in checks
    assert "/dev/kvm" not in checks
    assert not checks["scienceboard_vmrun"].ok


def test_vm_path_accepts_vmx(tmp_path: Path) -> None:
    root = tmp_path / "ScienceBoard"
    _init_git_repo(root)
    vmx = tmp_path / "Ubuntu.vmx"
    vmx.write_text(".encoding = \"UTF-8\"\n", encoding="utf-8")
    checks = {
        item.name: item
        for item in collect_preflight(
            root, "deadbeef", environ={"CUA_EVAL_SCIENCEBOARD_VM_PATH": str(vmx)}
        )
    }
    assert checks["scienceboard_vm"].ok


def test_normalize_keeps_official_float() -> None:
    bench = ScienceBoardBench()
    raw = RawResult(
        task_id=SMOKE_TASK,
        steps=3,
        wall_time_seconds=1.2,
        terminated=True,
        terminate_status="success",
        evaluator_score=1.0,
        failure_class=FailureClass.OK,
    )
    assert bench.normalize(raw).score == 1.0
    infra = RawResult(
        task_id=SMOKE_TASK,
        steps=0,
        wall_time_seconds=0.0,
        terminated=False,
        terminate_status=None,
        evaluator_score=None,
        failure_class=FailureClass.INFRA_ERROR,
        error_message="vm down",
    )
    assert bench.normalize(infra).score is None


def test_run_trial_uses_official_eval_not_tester(tmp_path: Path) -> None:
    _write_task_json(tmp_path)
    manager = _FakeManager(solid_color_png(64, 64))
    task = _FakeTask(manager)
    experiment = Experiment.from_yaml(SCI_YAML)
    bench = ScienceBoardBench(
        experiment,
        root=tmp_path,
        task_factory=lambda _task_id: task,
        skip_host_preflight=True,
    )
    raw = bench.run_trial(SMOKE_TASK, _TerminatingHarness())
    assert task.init_called
    assert task.eval_called
    assert raw.evaluator_score == 1.0
    assert raw.failure_class is FailureClass.OK
    bench.cleanup()
    assert manager.closed


def test_run_task_path_sets_scienceboard_guest_env(tmp_path: Path) -> None:
    _write_task_json(tmp_path)
    manager = _FakeManager(solid_color_png(64, 64))
    task = _FakeTask(manager)
    experiment = Experiment.from_yaml(SCI_YAML)
    bench = ScienceBoardBench(
        experiment,
        root=tmp_path,
        task_factory=lambda _task_id: task,
        skip_host_preflight=True,
    )
    harness = _RecordingHarness()
    raw = bench.run_trial(SMOKE_TASK, harness)  # type: ignore[arg-type]
    assert harness.extra_env is not None
    assert harness.extra_env["CUA_EVAL_MCP_BACKEND"] == "scienceboard"
    assert harness.extra_env["CUA_EVAL_SCIENCEBOARD_VM_IP"] == "10.0.0.3"
    assert raw.evaluator_score == 1.0


def test_guest_shell_uses_manager_not_host_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("shell 不得落到评测宿主机 subprocess")

    monkeypatch.setattr("subprocess.run", boom)
    manager = _FakeManager(solid_color_png(32, 32))
    guest = ScienceBoardGuest(manager)
    text = guest.run_bash("hostname", timeout=5)
    assert "sci-guest" in text
    assert manager.bash == ["hostname"]
    guest.click(10, 20, "left")
    assert manager.python
    assert "pyautogui.click" in manager.python[0]
