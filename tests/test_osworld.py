"""OSWorld adapter：无网、不下载 qcow2。真跑由 test_osworld_live 的 skip 门禁挡住。"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from cua_eval.actions import TerminateAction
from cua_eval.benches.base import RawResult, get_bench
from cua_eval.benches.fake import solid_color_png
from cua_eval.benches.osworld import (
    OSWORLD_DOCKER_IMAGE,
    OSWorldBench,
    collect_preflight,
    ubuntu_qcow2,
)
from cua_eval.benches.osworld_guest import OSWorldGuest
from cua_eval.errors import ConfigError
from cua_eval.harness.base import Completion, StepObservation
from cua_eval.schema import BenchId, Experiment, FailureClass

CONFIG_DIR = Path(__file__).resolve().parents[1] / "configs" / "experiments"
OSWORLD_YAML = CONFIG_DIR / "smoke_osworld.yaml"
SMOKE_TASK = "5ea617a3-0e86-4ba6-aab2-dac9aa2e8d57"


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


class _FakeController:
    def __init__(self, png: bytes) -> None:
        self.png = png
        self.python: list[str] = []
        self.bash: list[str] = []

    def get_screenshot(self) -> bytes:
        return self.png

    def execute_python_command(self, command: str) -> None:
        self.python.append(command)

    def run_bash_script(
        self, script: str, timeout: int = 30, working_dir: str | None = None
    ) -> dict[str, Any]:
        del timeout, working_dir
        self.bash.append(script)
        return {"output": "osworld-guest\n", "error": "", "returncode": 0}


class _FakeEnv:
    def __init__(self, controller: _FakeController) -> None:
        self.controller = controller
        self.vm_ip = "10.0.0.2"
        self.server_port = 5000
        self.closed = False
        self._step_no = 0
        self.actions: list[object] = []
        self.reset_configs: list[object] = []
        self.kwargs: dict[str, Any] = {}

    def reset(self, task_config: dict[str, Any] | None = None) -> dict[str, Any]:
        self.reset_configs.append(task_config)
        instruction = ""
        if isinstance(task_config, dict):
            instruction = str(task_config.get("instruction") or "")
        return {"screenshot": self.controller.png, "instruction": instruction}

    def step(
        self, action: object, pause: float = 2
    ) -> tuple[dict[str, Any], int, bool, dict[str, Any]]:
        del pause
        self.actions.append(action)
        self._step_no += 1
        return {}, 0, False, {}

    def evaluate(self) -> float:
        return 1.0

    def close(self) -> None:
        self.closed = True


def _write_task_json(root: Path) -> None:
    path = root / "evaluation_examples" / "examples" / "os" / f"{SMOKE_TASK}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "id": SMOKE_TASK,
                "instruction": "recover the poster from Trash",
                "evaluator": {"func": "exact_match"},
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
    (path / "README").write_text("osworld pin fixture\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=path, check=True, capture_output=True, text=True
    )
    return head.stdout.strip()


def test_get_bench_passes_experiment() -> None:
    experiment = Experiment.from_yaml(OSWORLD_YAML)
    bench = get_bench(experiment)
    assert isinstance(bench, OSWorldBench)
    assert bench.id == "osworld_verified"
    assert experiment.bench is BenchId.OSWORLD_VERIFIED


def test_prepare_fails_without_checkout_and_does_not_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("OSWorld adapter 禁止下载")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    experiment = Experiment.from_yaml(OSWORLD_YAML)
    missing = tmp_path / "no-such-osworld"
    with pytest.raises(ConfigError, match="不会退化成 dummy") as exc:
        OSWorldBench(experiment, root=missing).prepare()
    assert "qcow2" in str(exc.value) or "clone" in str(exc.value)
    assert not ubuntu_qcow2(missing).exists()


def test_prepare_refuses_download_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CUA_EVAL_OSWORLD_ALLOW_DOWNLOAD", "1")
    experiment = Experiment.from_yaml(OSWORLD_YAML)
    with pytest.raises(ConfigError, match="禁止自动下载"):
        OSWorldBench(experiment, root=tmp_path).prepare()


def test_preflight_names_missing_qcow2_and_image(tmp_path: Path) -> None:
    root = tmp_path / "OSWorld"
    _init_git_repo(root)
    checks = {item.name: item for item in collect_preflight(root, "deadbeef")}
    assert not checks["osworld_qcow2"].ok
    assert "Ubuntu.qcow2" in checks["osworld_qcow2"].detail
    assert "禁止下载" in checks["osworld_qcow2"].detail
    assert checks["osworld_image"].ok is False
    assert OSWORLD_DOCKER_IMAGE in checks["osworld_image"].detail


def test_pin_must_match_head(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "OSWorld"
    head = _init_git_repo(root)
    monkeypatch.setattr("cua_eval.benches.osworld.OSWORLD_MIN_COMMIT", head[:7])
    checks = {item.name: item for item in collect_preflight(root, head)}
    # still missing qcow2/docker/kvm, but commit should be ok because pin==HEAD and floor prefix
    assert checks["osworld_commit"].ok
    mismatch = {item.name: item for item in collect_preflight(root, "0" * 40)}
    assert not mismatch["osworld_commit"].ok
    detail = mismatch["osworld_commit"].detail
    assert "不要浮动 main" in detail or "没有 pin" in detail


def test_normalize_keeps_official_float() -> None:
    bench = OSWorldBench()
    raw = RawResult(
        task_id=SMOKE_TASK,
        steps=3,
        wall_time_seconds=1.2,
        terminated=True,
        terminate_status="success",
        evaluator_score=1.0,
        failure_class=FailureClass.OK,
    )
    trial = bench.normalize(raw)
    assert trial.score == 1.0
    assert trial.failure_class is FailureClass.OK

    raw_fail = raw.__class__(
        **{**raw.__dict__, "evaluator_score": 0.0, "failure_class": FailureClass.TASK_FAIL}
    )
    assert bench.normalize(raw_fail).score == 0.0

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
    trial_infra = bench.normalize(infra)
    assert trial_infra.score is None
    assert trial_infra.failure_class is FailureClass.INFRA_ERROR


def test_run_trial_uses_official_evaluate_and_writes_no_a11y(
    tmp_path: Path,
) -> None:
    _write_task_json(tmp_path)
    png = solid_color_png(64, 64)
    controller = _FakeController(png)
    env = _FakeEnv(controller)
    experiment = Experiment.from_yaml(OSWORLD_YAML)
    bench = OSWorldBench(
        experiment,
        root=tmp_path,
        env_factory=lambda: env,
        skip_host_preflight=True,
    )
    raw = bench.run_trial(SMOKE_TASK, _TerminatingHarness())
    assert raw.evaluator_score == 1.0
    assert raw.failure_class is FailureClass.OK
    assert env.reset_configs and env.reset_configs[0]["id"] == SMOKE_TASK
    assert "DONE" in env.actions
    assert raw.raw_artifacts is not None
    assert "task.json" in raw.raw_artifacts
    trial = bench.normalize(raw)
    assert trial.score == 1.0
    bench.cleanup()
    assert env.closed
    bench.cleanup()  # idempotent


def test_run_task_path_sets_osworld_guest_env(tmp_path: Path) -> None:
    _write_task_json(tmp_path)
    png = solid_color_png(64, 64)
    env = _FakeEnv(_FakeController(png))
    experiment = Experiment.from_yaml(OSWORLD_YAML)
    bench = OSWorldBench(
        experiment,
        root=tmp_path,
        env_factory=lambda: env,
        skip_host_preflight=True,
    )
    harness = _RecordingHarness()
    raw = bench.run_trial(SMOKE_TASK, harness)  # type: ignore[arg-type]
    assert harness.extra_env is not None
    assert harness.extra_env["CUA_EVAL_MCP_BACKEND"] == "osworld"
    assert harness.extra_env["CUA_EVAL_OSWORLD_VM_IP"] == "10.0.0.2"
    assert "osworld" in harness.extra_env["CUA_EVAL_MCP_BACKEND"]
    assert raw.evaluator_score == 1.0


def test_guest_shell_uses_controller_not_host_subprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("shell 不得落到评测宿主机 subprocess")

    monkeypatch.setattr("subprocess.run", boom)
    controller = _FakeController(solid_color_png(32, 32))
    guest = OSWorldGuest(controller)
    text = guest.run_bash("hostname", timeout=5)
    assert "osworld-guest" in text
    assert controller.bash == ["hostname"]
    guest.click(10, 20, "left")
    assert controller.python
    assert "pyautogui.click" in controller.python[0]


def test_open_env_passes_no_a11y_and_explicit_qcow2(tmp_path: Path) -> None:
    qcow2 = ubuntu_qcow2(tmp_path)
    qcow2.parent.mkdir(parents=True)
    qcow2.write_bytes(b"qcow2-placeholder")
    captured: dict[str, Any] = {}

    class _DesktopEnv:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    experiment = Experiment.from_yaml(OSWORLD_YAML)
    bench = OSWorldBench(experiment, root=tmp_path, skip_host_preflight=True)
    bench._load_desktop_env = lambda: _DesktopEnv  # type: ignore[method-assign]
    env = bench._ensure_env()
    assert isinstance(env, _DesktopEnv)
    assert captured["require_a11y_tree"] is False
    assert captured["require_terminal"] is False
    assert captured["provider_name"] == "docker"
    assert captured["path_to_vm"] == str(qcow2.resolve())
    assert captured["action_space"] == "pyautogui"
