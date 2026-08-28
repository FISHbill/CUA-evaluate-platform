"""OSWorld-Verified adapter：包装官方 Docker provider 与 evaluator。

不把 OSWorld 源码复制进本仓库；官方仓库 checkout 到 `third_party/OSWorld`。
**不会**下载 qcow2 / 拉 Docker 镜像。缺文件就在 prepare() 失败。
打分只调用 `DesktopEnv.evaluate()`，不重写 metric。
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cua_eval.actions import ShellAction, TerminateAction, to_pyautogui, validate_against_protocol
from cua_eval.benches.base import RawResult
from cua_eval.benches.osworld_guest import OSWorldGuest, ensure_osworld_on_path
from cua_eval.errors import ConfigError, InfraError
from cua_eval.harness.base import Harness, StepObservation
from cua_eval.schema import (
    OSWORLD_MIN_COMMIT,
    Experiment,
    FailureClass,
    Observation,
    TrialResult,
)

OSWORLD_DOCKER_IMAGE = "happysixd/osworld-docker"
QCOW2_NAME = "Ubuntu.qcow2"
VMS_DIRNAME = "docker_vm_data"
CLONE_HINT = "git clone https://github.com/xlang-ai/OSWorld.git third_party/OSWorld"


@dataclass(frozen=True)
class Prereq:
    name: str
    ok: bool
    detail: str


def package_repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").is_file() and (parent / "src" / "cua_eval").is_dir():
            return parent
    return Path.cwd()


def default_osworld_root() -> Path:
    override = os.environ.get("CUA_EVAL_OSWORLD_ROOT")
    if override:
        return Path(override)
    return package_repo_root() / "third_party" / "OSWorld"


def ubuntu_qcow2(root: Path) -> Path:
    return root / VMS_DIRNAME / QCOW2_NAME


def task_json_path(root: Path, task_id: str) -> Path | None:
    examples = root / "evaluation_examples" / "examples"
    if not examples.is_dir():
        return None
    matches = sorted(examples.glob(f"**/{task_id}.json"))
    return matches[0] if matches else None


def _git(root: Path, *args: str, timeout: float = 8.0) -> subprocess.CompletedProcess[str]:
    git = shutil.which("git")
    if git is None:
        return subprocess.CompletedProcess(
            args=("git", *args), returncode=127, stdout="", stderr="git not found"
        )
    return subprocess.run(
        [git, "-C", str(root), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _docker(*args: str, timeout: float = 8.0) -> subprocess.CompletedProcess[str]:
    docker = shutil.which("docker")
    if docker is None:
        return subprocess.CompletedProcess(
            args=("docker", *args), returncode=127, stdout="", stderr="docker not found"
        )
    return subprocess.run(
        [docker, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def kvm_readable() -> bool:
    path = Path("/dev/kvm")
    return path.exists() and os.access(path, os.R_OK)


def collect_preflight(root: Path, pin: str) -> list[Prereq]:
    """本地前置检查。任何一项失败都不得开始下载或起 VM。"""
    checks: list[Prereq] = []
    if not root.is_dir():
        checks.append(
            Prereq(
                "osworld_checkout",
                False,
                f"{root} 不存在。请 {CLONE_HINT} 并 checkout 到 pin {pin}。"
                "adapter 不会自动 clone，也不会下载 qcow2。不要退化成 dummy。",
            )
        )
    elif not (root / ".git").exists():
        checks.append(
            Prereq(
                "osworld_checkout",
                False,
                f"{root} 不是 git 仓库。请用官方 checkout，不要把源码复制进本仓库。",
            )
        )
    else:
        checks.append(Prereq("osworld_checkout", True, str(root)))
        checks.append(_verify_pin(root, pin))

    qcow2 = ubuntu_qcow2(root)
    checks.append(
        Prereq(
            "osworld_qcow2",
            qcow2.is_file(),
            (
                str(qcow2)
                if qcow2.is_file()
                else (
                    f"缺少 {qcow2}。把官方 Ubuntu.qcow2 放到 docker_vm_data/ 后再跑；"
                    "adapter 禁止下载 qcow2，CI 也禁止拉 VM 镜像。"
                )
            ),
        )
    )

    docker = shutil.which("docker")
    if docker is None:
        checks.append(Prereq("docker_daemon", False, "未安装 Docker。OSWorld 真跑需要 Docker。"))
        checks.append(
            Prereq(
                "osworld_image",
                False,
                f"无法检查镜像 {OSWORLD_DOCKER_IMAGE}（没有 docker）。不要在 CI 里 docker pull。",
            )
        )
    else:
        info = _docker("info")
        daemon_ok = info.returncode == 0
        checks.append(
            Prereq(
                "docker_daemon",
                daemon_ok,
                docker if daemon_ok else "Docker 守护进程不可用（docker info 失败）。",
            )
        )
        inspect = _docker("image", "inspect", OSWORLD_DOCKER_IMAGE)
        image_ok = inspect.returncode == 0
        checks.append(
            Prereq(
                "osworld_image",
                image_ok,
                (
                    OSWORLD_DOCKER_IMAGE
                    if image_ok
                    else (
                        f"镜像未就位：{OSWORLD_DOCKER_IMAGE}。"
                        "请在执行机上手动 docker pull；CI 禁止拉 VM 镜像。"
                    )
                ),
            )
        )

    checks.append(
        Prereq(
            "/dev/kvm",
            kvm_readable(),
            (
                "当前用户可读"
                if kvm_readable()
                else "不存在或当前用户不可读。无 KVM 时桌面 VM 会慢一个数量级，不适合作为主路径。"
            ),
        )
    )
    return checks


def _verify_pin(root: Path, pin: str) -> Prereq:
    if shutil.which("git") is None:
        return Prereq("osworld_commit", False, "需要 git 核对 commit pin")
    try:
        head_p = _git(root, "rev-parse", "HEAD")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return Prereq("osworld_commit", False, f"读 HEAD 失败: {exc}")
    if head_p.returncode != 0:
        return Prereq("osworld_commit", False, "无法读取 HEAD")
    head = head_p.stdout.strip()
    resolved_p = _git(root, "rev-parse", "--verify", f"{pin}^{{commit}}")
    if resolved_p.returncode != 0:
        resolved_p = _git(root, "rev-parse", "--verify", pin)
    if resolved_p.returncode != 0:
        return Prereq("osworld_commit", False, f"checkout 里没有 pin {pin}")
    resolved = resolved_p.stdout.strip()
    if resolved != head:
        return Prereq(
            "osworld_commit",
            False,
            f"HEAD={head[:12]} 与配置 pin {pin} 不一致；不要浮动 main。",
        )
    try:
        ancestor = _git(root, "merge-base", "--is-ancestor", OSWORLD_MIN_COMMIT, "HEAD")
    except (OSError, subprocess.TimeoutExpired):
        ancestor = subprocess.CompletedProcess(args=(), returncode=1, stdout="", stderr="")
    pin_is_floor = pin.startswith(OSWORLD_MIN_COMMIT) or OSWORLD_MIN_COMMIT.startswith(pin[:7])
    if ancestor.returncode != 0 and not pin_is_floor:
        return Prereq(
            "osworld_commit",
            False,
            f"HEAD 不是 {OSWORLD_MIN_COMMIT} 的后代（低于此版本每题泄漏约 32 GB 匿名卷）。",
        )
    return Prereq("osworld_commit", True, f"{head[:12]} (>= {OSWORLD_MIN_COMMIT})")


def require_ready(root: Path, pin: str) -> None:
    failed = [item for item in collect_preflight(root, pin) if not item.ok]
    if not failed:
        return
    lines = "; ".join(f"{item.name}: {item.detail}" for item in failed)
    raise ConfigError(
        "OSWorld 前置检查未通过（不会退化成 dummy，也不会下载 qcow2）：" + lines
    )


def load_task_config(root: Path, task_id: str) -> dict[str, Any]:
    path = task_json_path(root, task_id)
    if path is None:
        raise ConfigError(
            f"在 {root}/evaluation_examples/examples 下找不到任务 {task_id}.json"
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"读任务 JSON {path} 失败: {exc}") from exc
    if not isinstance(payload, dict):
        raise ConfigError(f"{path} 的顶层必须是对象")
    return payload


def _score_class(score: float) -> FailureClass:
    return FailureClass.OK if score >= 1.0 else FailureClass.TASK_FAIL


class OSWorldBench:
    """官方 DesktopEnv 的薄包装。prepare 只做前置检查，不起 VM。"""

    id = "osworld_verified"

    def __init__(
        self,
        experiment: Experiment | None = None,
        *,
        root: Path | None = None,
        env_factory: Callable[[], Any] | None = None,
        skip_host_preflight: bool = False,
    ) -> None:
        self._experiment = experiment
        self._root = Path(root) if root is not None else default_osworld_root()
        self._env_factory = env_factory
        self._skip_host_preflight = skip_host_preflight
        self._env: Any = None

    def _pin(self) -> str:
        if self._experiment is not None and self._experiment.bench_version:
            return self._experiment.bench_version
        return OSWORLD_MIN_COMMIT

    def prepare(self) -> None:
        if os.environ.get("CUA_EVAL_OSWORLD_ALLOW_DOWNLOAD") == "1":
            raise ConfigError(
                "adapter 禁止自动下载 qcow2 / 拉镜像，即使设置了 "
                "CUA_EVAL_OSWORLD_ALLOW_DOWNLOAD。把 Ubuntu.qcow2 放到 docker_vm_data/。"
            )
        if not self._skip_host_preflight:
            require_ready(self._root, self._pin())
        ensure_osworld_on_path(self._root)

    def list_tasks(self) -> list[str]:
        if self._experiment is not None:
            return list(self._experiment.task_ids)
        return []

    def run_trial(self, task_id: str, agent: Harness) -> RawResult:
        start = time.monotonic()
        try:
            env = self._ensure_env()
            task = load_task_config(self._root, task_id)
            observation = env.reset(task_config=task)
        except ConfigError:
            raise
        except Exception as exc:
            raise InfraError(f"OSWorld reset 失败（环境故障，不得记成模型 0 分）: {exc}") from exc

        instruction = ""
        if isinstance(observation, dict):
            raw_instruction = observation.get("instruction")
            if isinstance(raw_instruction, str):
                instruction = raw_instruction
        if not instruction:
            instruction = str(task.get("instruction") or f"OSWorld task {task_id}")

        extra_env = {
            "CUA_EVAL_MCP_BACKEND": "osworld",
            "CUA_EVAL_OSWORLD_ROOT": str(self._root),
            "CUA_EVAL_OSWORLD_VM_IP": str(getattr(env, "vm_ip", "")),
            "CUA_EVAL_OSWORLD_SERVER_PORT": str(getattr(env, "server_port", 5000)),
            "PYTHONPATH": os.pathsep.join(
                [str(self._root), os.environ.get("PYTHONPATH", "")]
            ).rstrip(os.pathsep),
        }
        run_task = getattr(agent, "run_task", None)
        try:
            if callable(run_task):
                work_dir = Path(tempfile.mkdtemp(prefix="cua-eval-osworld-"))
                try:
                    run_task(instruction, work_dir=work_dir, extra_env=extra_env)
                finally:
                    shutil.rmtree(work_dir, ignore_errors=True)
                steps = int(getattr(env, "_step_no", 0) or 0)
            else:
                steps = self._step_loop(env, agent, task_id, instruction)
            score = self._official_score(env)
        except (InfraError, ConfigError):
            raise
        except Exception as exc:
            raise InfraError(f"OSWorld trial 失败（环境故障，不得记成模型 0 分）: {exc}") from exc

        wall = time.monotonic() - start
        failure = _score_class(score)
        raw_artifacts = {
            "task.json": json.dumps(task, ensure_ascii=False, indent=2) + "\n",
            "evaluator.json": json.dumps({"score": score, "task_id": task_id}, indent=2) + "\n",
        }
        return RawResult(
            task_id=task_id,
            steps=steps,
            wall_time_seconds=wall,
            terminated=True,
            terminate_status="success" if failure is FailureClass.OK else "fail",
            evaluator_score=score,
            failure_class=failure,
            raw_artifacts=raw_artifacts,
        )

    def normalize(self, raw: RawResult) -> TrialResult:
        if raw.failure_class is FailureClass.INFRA_ERROR:
            return TrialResult(
                task_id=raw.task_id,
                failure_class=FailureClass.INFRA_ERROR,
                score=None,
                steps=raw.steps,
                wall_time_seconds=raw.wall_time_seconds,
                error_message=raw.error_message,
            )
        if raw.evaluator_score is None:
            raise ConfigError("官方 evaluator 没有返回分数")
        score = float(raw.evaluator_score)
        return TrialResult(
            task_id=raw.task_id,
            failure_class=raw.failure_class,
            score=score,
            steps=raw.steps,
            wall_time_seconds=raw.wall_time_seconds,
            input_tokens=raw.input_tokens,
            output_tokens=raw.output_tokens,
            error_message=raw.error_message,
        )

    def cleanup(self) -> None:
        env = self._env
        self._env = None
        if env is not None:
            closer = getattr(env, "close", None)
            if callable(closer):
                with contextlib.suppress(Exception):
                    closer()
        _best_effort_remove_containers()

    def _ensure_env(self) -> Any:
        if self._env is not None:
            return self._env
        if self._env_factory is not None:
            self._env = self._env_factory()
            return self._env
        qcow2 = ubuntu_qcow2(self._root)
        if not qcow2.is_file():
            raise ConfigError(f"缺少 {qcow2}。adapter 不会下载 qcow2。")
        desktop_env_cls = self._load_desktop_env()
        try:
            self._env = desktop_env_cls(
                provider_name="docker",
                path_to_vm=str(qcow2.resolve()),
                action_space="pyautogui",
                screen_size=(1920, 1080),
                headless=True,
                require_a11y_tree=False,
                require_terminal=False,
                os_type="Ubuntu",
                cache_dir=str(self._root / "cache"),
            )
        except Exception as exc:
            raise InfraError(f"启动 OSWorld DesktopEnv 失败: {exc}") from exc
        return self._env

    def _load_desktop_env(self) -> Any:
        ensure_osworld_on_path(self._root)
        try:
            from desktop_env.desktop_env import DesktopEnv
        except ImportError as exc:
            raise ConfigError(
                f"导入不了 desktop_env（root={self._root}）。"
                "请 checkout 官方 OSWorld 到 third_party/OSWorld。"
            ) from exc
        return DesktopEnv

    def _official_score(self, env: Any) -> float:
        try:
            metric = env.evaluate()
        except Exception as exc:
            raise InfraError(f"官方 evaluator 执行失败（不得记成模型 0 分）: {exc}") from exc
        try:
            return float(metric)
        except (TypeError, ValueError) as exc:
            raise InfraError(f"官方 evaluator 返回了无法解析的分数: {metric!r}") from exc

    def _step_loop(self, env: Any, agent: Harness, task_id: str, instruction: str) -> int:
        if self._experiment is None:
            raise ConfigError("OSWorldBench.run_trial 需要 Experiment（limits / protocol）")
        limits = self._experiment.agent.limits
        protocol = self._experiment.agent.protocol
        controller = getattr(env, "controller", None)
        if controller is None:
            raise InfraError("DesktopEnv 没有 controller")
        guest = OSWorldGuest(controller)
        deadline = time.monotonic() + limits.task_timeout_seconds
        steps = 0
        for step in range(limits.max_steps):
            if time.monotonic() > deadline:
                raise InfraError(
                    f"task {task_id} exceeded wall-clock limit ({limits.task_timeout_seconds}s)"
                )
            png: bytes | None = None
            width, height = 1920, 1080
            if protocol.observation is Observation.SCREENSHOT:
                png, width, height = guest.capture_png()
            observation = StepObservation(
                instruction=instruction,
                step_index=step,
                screenshot_png=png,
                screenshot_width=width,
                screenshot_height=height,
            )
            completion = agent.act(observation)
            validate_against_protocol(completion.action, protocol)
            steps = step + 1
            action = completion.action
            if isinstance(action, TerminateAction):
                done_token = "DONE" if action.status == "success" else "FAIL"
                try:
                    env.step(done_token, pause=0)
                except Exception:
                    guest.terminate(action.status)
                break
            if isinstance(action, ShellAction):
                guest.run_bash(action.command, action.timeout)
                continue
            try:
                env.step(to_pyautogui(action), pause=0.5)
            except ValueError:
                guest.terminate("fail")
                break
        return steps


def _best_effort_remove_containers() -> None:
    """close() 之后再扫一遍官方镜像的残留容器，带 -v 清匿名卷。"""
    if shutil.which("docker") is None:
        return
    try:
        listed = _docker("ps", "-aq", "--filter", f"ancestor={OSWORLD_DOCKER_IMAGE}")
    except (OSError, subprocess.TimeoutExpired):
        return
    ids = [line.strip() for line in listed.stdout.splitlines() if line.strip()]
    if not ids:
        return
    try:
        _docker("rm", "-fv", *ids, timeout=30.0)
    except (OSError, subprocess.TimeoutExpired):
        return


__all__ = [
    "CLONE_HINT",
    "OSWORLD_DOCKER_IMAGE",
    "OSWorldBench",
    "Prereq",
    "collect_preflight",
    "default_osworld_root",
    "load_task_config",
    "package_repo_root",
    "require_ready",
    "task_json_path",
    "ubuntu_qcow2",
]
