"""MacAgentBench adapter：无网、不下载 HDD。真跑由 skip 门禁挡住。"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from cua_eval.actions import TerminateAction
from cua_eval.benches.base import RawResult, get_bench
from cua_eval.benches.fake import solid_color_png
from cua_eval.benches.lucwei_mac import LucweiMacGuest, screenshot_url
from cua_eval.benches.mac_agent_bench import (
    MacAgentBench,
    coerce_official_score,
    collect_preflight,
    require_ready,
)
from cua_eval.errors import ConfigError
from cua_eval.harness.base import Completion, StepObservation
from cua_eval.harness.guest import build_guest
from cua_eval.schema import BenchId, Experiment, FailureClass

CONFIG_DIR = Path(__file__).resolve().parents[1] / "configs" / "experiments"
MAC_YAML = CONFIG_DIR / "smoke_mac_agent_bench.yaml"
SMOKE_TASK = "clock/1_1"


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


class _FakeMacEnv:
    def __init__(self, png: bytes) -> None:
        self.png = png
        self.init_paths: list[str] = []
        self.actions: list[object] = []
        self.closed = False
        self._step_no = 0
        self.native_width = 64
        self.native_height = 64

    def init_task(self, task_json_path: str) -> None:
        self.init_paths.append(task_json_path)

    def capture_png(self) -> tuple[bytes, int, int]:
        return self.png, 64, 64

    def step(self, action: object, pause: float = 2) -> tuple[dict[str, Any], int, bool, dict[str, Any]]:
        del pause
        self.actions.append(action)
        self._step_no += 1
        return {}, 0, False, {}

    def evaluate_task(self) -> bool:
        return True

    def terminate(self, status: str) -> None:
        self.actions.append(("terminate", status))

    def run_bash(self, command: str, timeout: float | None) -> str:
        del timeout
        return f"mac-guest\n# {command}\n"

    def close_connection(self) -> None:
        self.closed = True


def _write_task_json(root: Path) -> None:
    path = root / "tasks" / "clock" / "1_1.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "id": "1_1",
                "instruction": "set a weekday alarm in Clock",
                "evaluator": {"func": ["clock_list_alarms"]},
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
    (path / "README").write_text("mac pin fixture\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=path, check=True, capture_output=True, text=True
    )
    return head.stdout.strip()


def test_get_bench_passes_experiment() -> None:
    experiment = Experiment.from_yaml(MAC_YAML)
    bench = get_bench(experiment)
    assert isinstance(bench, MacAgentBench)
    assert bench.id == "mac_agent_bench"
    assert experiment.bench is BenchId.MAC_AGENT_BENCH
    assert experiment.task_ids == [SMOKE_TASK]


def test_prepare_fails_without_checkout_and_does_not_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("MacAgentBench adapter 禁止下载")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    experiment = Experiment.from_yaml(MAC_YAML)
    missing = tmp_path / "no-such-mac"
    with pytest.raises(ConfigError, match="不会退化成 dummy") as exc:
        MacAgentBench(experiment, root=missing, environ={}).prepare()
    assert "clone" in str(exc.value) or "checkout" in str(exc.value) or "Fleet" in str(exc.value)


def test_prepare_refuses_download_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CUA_EVAL_MACAGENTBENCH_ALLOW_DOWNLOAD", "1")
    experiment = Experiment.from_yaml(MAC_YAML)
    with pytest.raises(ConfigError, match="禁止自动下载"):
        MacAgentBench(experiment, root=tmp_path).prepare()


def test_preflight_does_not_require_local_kvm(tmp_path: Path) -> None:
    root = tmp_path / "MacAgentBench"
    _init_git_repo(root)
    checks = {item.name: item for item in collect_preflight(root, "deadbeef", environ={})}
    assert checks["mac_agent_bench_hdd"].ok
    assert not checks["mac_fleet_url"].ok
    assert "禁止下载" in checks["mac_agent_bench_hdd"].detail or "远程" in checks["mac_agent_bench_hdd"].detail
    assert "/dev/kvm" not in checks


def test_require_ready_names_missing_fleet(tmp_path: Path) -> None:
    root = tmp_path / "MacAgentBench"
    _init_git_repo(root)
    with pytest.raises(ConfigError, match="Fleet"):
        require_ready(root, "0" * 40, environ={})


def test_normalize_keeps_official_float() -> None:
    bench = MacAgentBench()
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
    trial_infra = bench.normalize(infra)
    assert trial_infra.score is None
    assert trial_infra.failure_class is FailureClass.INFRA_ERROR


def test_coerce_bool_and_score_object() -> None:
    assert coerce_official_score(True) == 1.0
    assert coerce_official_score(False) == 0.0

    class _Eval:
        score = 0.75

    assert coerce_official_score(_Eval()) == 0.75


def test_run_trial_uses_official_evaluate(tmp_path: Path) -> None:
    _write_task_json(tmp_path)
    png = solid_color_png(64, 64)
    env = _FakeMacEnv(png)
    experiment = Experiment.from_yaml(MAC_YAML)
    bench = MacAgentBench(
        experiment,
        root=tmp_path,
        env_factory=lambda: env,
        skip_host_preflight=True,
    )
    raw = bench.run_trial(SMOKE_TASK, _TerminatingHarness())
    assert raw.evaluator_score == 1.0
    assert raw.failure_class is FailureClass.OK
    assert env.init_paths and env.init_paths[0].endswith("1_1.json")
    assert "DONE" in env.actions
    bench.cleanup()
    assert env.closed


def test_run_task_path_sets_lucwei_guest_env(tmp_path: Path) -> None:
    _write_task_json(tmp_path)
    env = _FakeMacEnv(solid_color_png(64, 64))
    experiment = Experiment.from_yaml(MAC_YAML)
    bench = MacAgentBench(
        experiment,
        root=tmp_path,
        env_factory=lambda: env,
        skip_host_preflight=True,
        environ={
            "CUA_EVAL_MAC_FLEET_URL": "http://127.0.0.1:9",
            "CUA_EVAL_MAC_POOL": "unit-pool",
            "CUA_EVAL_MAC_VM_UUID": "00000000-0000-0000-0000-000000000001",
        },
    )
    harness = _RecordingHarness()
    raw = bench.run_trial(SMOKE_TASK, harness)  # type: ignore[arg-type]
    assert harness.extra_env is not None
    assert harness.extra_env["CUA_EVAL_MCP_BACKEND"] == "lucwei_mac"
    assert harness.extra_env["CUA_EVAL_MAC_POOL"] == "unit-pool"
    assert raw.evaluator_score == 1.0


def test_lucwei_shell_uses_ssh_runner_not_host_subprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("shell 不得落到评测宿主机 subprocess")

    monkeypatch.setattr("subprocess.run", boom)
    calls: list[str] = []

    def runner(command: str, timeout: float) -> str:
        del timeout
        calls.append(command)
        if command.strip() == "hostname":
            return "mac-guest\n"
        return f"[guest] {command}\n"

    guest = LucweiMacGuest(
        base_url="http://127.0.0.1:9",
        pool="unit-pool",
        vm_uuid="abc",
        ssh_host="127.0.0.1",
        ssh_user="tester",
        ssh_runner=runner,
        http_getter=lambda *_a, **_k: (200, solid_color_png(8, 8), "image/png"),
    )
    assert guest.run_bash("hostname", timeout=5).startswith("mac-guest")
    assert calls == ["hostname"]
    guest.click(10, 20, "left")
    assert any("pyautogui" in item for item in calls)


def test_build_guest_lucwei_from_env() -> None:
    guest = build_guest(
        {
            "CUA_EVAL_MCP_BACKEND": "lucwei_mac",
            "CUA_EVAL_MAC_FLEET_URL": "http://127.0.0.1:9",
            "CUA_EVAL_MAC_POOL": "unit-pool",
            "CUA_EVAL_MAC_VM_UUID": "abc",
            "CUA_EVAL_MAC_SSH_HOST": "127.0.0.1",
            "CUA_EVAL_MAC_SSH_USER": "tester",
        }
    )
    assert isinstance(guest, LucweiMacGuest)
    assert screenshot_url(guest.base_url, guest.vm_uuid).endswith("/screenshot")
