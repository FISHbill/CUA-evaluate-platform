"""ScienceBoard adapter：包装官方 VMTask.eval()，不调用 Tester() 整段 agent。

不把官方源码复制进本仓库；checkout 到 `third_party/ScienceBoard`。
**不会**下载 HF 上的 VM.zip。缺 checkout / 缺 .vmx 就在 prepare() 失败。
打分只调用官方 `task.eval()`，不重写 metric。
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from cua_eval.actions import ShellAction, TerminateAction, validate_against_protocol
from cua_eval.benches.base import RawResult
from cua_eval.benches.gitpin import Prereq, checkout_prereq, ensure_on_path, third_party_root
from cua_eval.benches.scienceboard_guest import ScienceBoardGuest
from cua_eval.errors import ConfigError, InfraError
from cua_eval.harness.base import Harness, StepObservation
from cua_eval.schema import Experiment, FailureClass, Observation, TrialResult

CLONE_HINT = "git clone https://github.com/OS-Copilot/ScienceBoard.git third_party/ScienceBoard"
ROOT_ENV = "CUA_EVAL_SCIENCEBOARD_ROOT"
VM_PATH_ENV = "CUA_EVAL_SCIENCEBOARD_VM_PATH"


def default_root() -> Path:
    return third_party_root("ScienceBoard", ROOT_ENV)


def vm_path(environ: dict[str, str] | None = None) -> Path | None:
    env = environ if environ is not None else dict(os.environ)
    raw = (env.get(VM_PATH_ENV) or env.get("VM_PATH") or "").strip()
    if not raw:
        return None
    return Path(raw)


def task_json_path(root: Path, task_id: str) -> Path | None:
    base = root / "tasks" / "VM"
    if not base.is_dir():
        return None
    relative = Path(*task_id.split("/")).with_suffix(".json")
    candidate = base / relative
    if candidate.is_file():
        return candidate
    matches = sorted(base.glob(f"**/{Path(task_id).name}.json"))
    return matches[0] if matches else None


def _vm_ready(path: Path) -> tuple[bool, str]:
    if not path.exists():
        return (
            False,
            f"{path} 不存在。把官方 VM.vmx（或已下载的 VM.zip）放到该路径；adapter 禁止下载。",
        )
    if path.is_file() and path.suffix.lower() == ".zip":
        return True, f"{path}（zip 已就位；adapter 不会再下载）"
    if path.is_file() and path.suffix.lower() == ".vmx":
        return True, str(path)
    if path.is_dir():
        vmx = sorted(path.glob("*.vmx"))
        if vmx:
            return True, str(vmx[0])
        return False, f"{path} 目录下没有 .vmx"
    return False, f"{path} 不是 .vmx / .zip / 含 vmx 的目录"


def collect_preflight(
    root: Path,
    pin: str,
    *,
    environ: dict[str, str] | None = None,
) -> list[Prereq]:
    env = environ if environ is not None else dict(os.environ)
    checks = checkout_prereq(
        root, pin=pin, clone_hint=CLONE_HINT, check_name="scienceboard_checkout"
    )
    path = vm_path(env)
    if path is None:
        checks.append(
            Prereq(
                "scienceboard_vm",
                False,
                f"未设置 {VM_PATH_ENV} 或 VM_PATH。官方环境盘是 VMware，"
                "adapter 禁止下载 VM.zip，不要退化成 dummy。",
            )
        )
    else:
        ok, detail = _vm_ready(path)
        checks.append(Prereq("scienceboard_vm", ok, detail))

    vmrun = shutil.which("vmrun")
    checks.append(
        Prereq(
            "scienceboard_vmrun",
            vmrun is not None,
            vmrun
            or "未找到 vmrun。ScienceBoard 走 VMware DesktopEnv，不是 OSWorld 的 Docker/qcow2。",
        )
    )
    return checks


def require_ready(root: Path, pin: str, *, environ: dict[str, str] | None = None) -> None:
    failed = [item for item in collect_preflight(root, pin, environ=environ) if not item.ok]
    if not failed:
        return
    lines = "; ".join(f"{item.name}: {item.detail}" for item in failed)
    raise ConfigError(
        "ScienceBoard 前置检查未通过（不会退化成 dummy，也不会下载 VM.zip）：" + lines
    )


def load_task_config(root: Path, task_id: str) -> dict[str, Any]:
    path = task_json_path(root, task_id)
    if path is None:
        raise ConfigError(f"在 {root}/tasks/VM 下找不到任务 {task_id}.json")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"读任务 JSON {path} 失败: {exc}") from exc
    if not isinstance(payload, dict):
        raise ConfigError(f"{path} 的顶层必须是对象")
    return payload


def _score_class(passed: bool) -> FailureClass:
    return FailureClass.OK if passed else FailureClass.TASK_FAIL


class ScienceBoardBench:
    """官方 VMTask.eval 的薄包装。禁止调用 Tester()（那会跑他们自己的 agent）。"""

    id = "scienceboard"

    def __init__(
        self,
        experiment: Experiment | None = None,
        *,
        root: Path | None = None,
        task_factory: Callable[[str], Any] | None = None,
        skip_host_preflight: bool = False,
        environ: dict[str, str] | None = None,
    ) -> None:
        self._experiment = experiment
        self._root = Path(root) if root is not None else default_root()
        self._task_factory = task_factory
        self._skip_host_preflight = skip_host_preflight
        self._environ = environ
        self._task: Any = None

    def _env_map(self) -> dict[str, str]:
        return dict(self._environ) if self._environ is not None else dict(os.environ)

    def _pin(self) -> str:
        if self._experiment is not None and self._experiment.bench_version:
            return self._experiment.bench_version
        return "c8d5010"

    def prepare(self) -> None:
        if os.environ.get("CUA_EVAL_SCIENCEBOARD_ALLOW_DOWNLOAD") == "1":
            raise ConfigError(
                "adapter 禁止自动下载 ScienceBoard VM.zip，即使设置了 "
                "CUA_EVAL_SCIENCEBOARD_ALLOW_DOWNLOAD。"
            )
        if not self._skip_host_preflight:
            require_ready(self._root, self._pin(), environ=self._env_map())
        ensure_on_path(self._root)

    def list_tasks(self) -> list[str]:
        if self._experiment is not None:
            return list(self._experiment.task_ids)
        return []

    def run_trial(self, task_id: str, agent: Harness) -> RawResult:
        start = time.monotonic()
        config = load_task_config(self._root, task_id)
        instruction = str(config.get("instruction") or f"ScienceBoard task {task_id}")
        try:
            task = self._load_task(task_id)
            initializer = getattr(task, "init", None)
            if callable(initializer):
                ok = initializer()
                if ok is False:
                    raise InfraError(
                        "ScienceBoard task.init() 失败（环境故障，不得记成模型 0 分）"
                    )
        except ConfigError:
            raise
        except InfraError:
            raise
        except Exception as exc:
            raise InfraError(
                f"ScienceBoard 任务初始化失败（环境故障，不得记成模型 0 分）: {exc}"
            ) from exc

        extra_env = self._guest_env(task)
        run_task = getattr(agent, "run_task", None)
        try:
            if callable(run_task):
                work_dir = Path(tempfile.mkdtemp(prefix="cua-eval-sci-"))
                try:
                    run_task(instruction, work_dir=work_dir, extra_env=extra_env)
                finally:
                    shutil.rmtree(work_dir, ignore_errors=True)
                steps = int(getattr(task, "steps_run", 0) or 0)
            else:
                steps = self._step_loop(task, agent, task_id, instruction)
            passed = self._official_eval(task)
        except (InfraError, ConfigError):
            raise
        except Exception as exc:
            raise InfraError(
                f"ScienceBoard trial 失败（环境故障，不得记成模型 0 分）: {exc}"
            ) from exc

        wall = time.monotonic() - start
        score = 1.0 if passed else 0.0
        failure = _score_class(passed)
        raw_artifacts = {
            "task.json": json.dumps(config, ensure_ascii=False, indent=2) + "\n",
            "evaluator.json": json.dumps(
                {"passed": passed, "score": score, "task_id": task_id}, indent=2
            )
            + "\n",
        }
        return RawResult(
            task_id=task_id,
            steps=steps,
            wall_time_seconds=wall,
            terminated=True,
            terminate_status="success" if passed else "fail",
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
        task = self._task
        self._task = None
        if task is None:
            return
        manager = getattr(task, "manager", None)
        if manager is None:
            return
        closer = getattr(manager, "__exit__", None)
        if callable(closer):
            try:
                closer(None, None, None)
            except Exception:
                return

    def _guest_env(self, task: Any) -> dict[str, str]:
        manager = getattr(task, "manager", None)
        vm_ip = ""
        server_port = "5000"
        if manager is not None:
            vm_ip = str(getattr(manager, "vm_ip", "") or "")
            controller = getattr(manager, "controller", None)
            if controller is not None:
                vm_ip = vm_ip or str(getattr(controller, "vm_ip", "") or "")
                server_port = str(getattr(controller, "server_port", server_port))
            env_obj = getattr(manager, "env", None)
            if env_obj is not None:
                vm_ip = vm_ip or str(getattr(env_obj, "vm_ip", "") or "")
                server_port = str(getattr(env_obj, "server_port", server_port))
        return {
            "CUA_EVAL_MCP_BACKEND": "scienceboard",
            "CUA_EVAL_SCIENCEBOARD_ROOT": str(self._root),
            "CUA_EVAL_SCIENCEBOARD_VM_IP": vm_ip,
            "CUA_EVAL_SCIENCEBOARD_SERVER_PORT": str(server_port),
        }

    def _load_task(self, task_id: str) -> Any:
        if self._task is not None:
            return self._task
        if self._task_factory is not None:
            self._task = self._task_factory(task_id)
            return self._task
        self._task = self._load_official_task(task_id)
        return self._task

    def _load_official_task(self, task_id: str) -> Any:
        path = task_json_path(self._root, task_id)
        if path is None:
            raise ConfigError(f"找不到任务 JSON: {task_id}")
        vm = vm_path(self._env_map())
        if vm is None:
            raise ConfigError(f"未设置 {VM_PATH_ENV} / VM_PATH")
        ensure_on_path(self._root)
        try:
            from dataclasses import dataclass

            from sci import Presets
            from sci import Task as BaseTask
            from sci.base.community import Community
        except ImportError as exc:
            raise ConfigError(
                f"导入不了 sci（root={self._root}）。请 checkout 官方 ScienceBoard。"
            ) from exc
        try:
            type_sort = BaseTask(config_path=str(path)).type_sort
            modules = Presets.spawn_modules()
            pkg = modules[type_sort.type]
            manager_cls = getattr(pkg, type_sort("Manager"))
            task_cls = getattr(pkg, type_sort("Task"))
            handle_managers = Presets.spawn_managers(True, str(vm))
            manager_args = handle_managers[type_sort]()
            manager = manager_cls(**manager_args)

            @dataclass
            class IdleCommunity(Community):  # type: ignore[misc]
                def __call__(self, *args: object, **kwargs: object) -> list[object]:
                    del args, kwargs
                    raise ConfigError(
                        "ScienceBoard adapter 不调用官方 Community/agent；请用本平台 harness。"
                    )

            return task_cls(
                config_path=str(path),
                manager=manager,
                community=IdleCommunity(),
                debug=False,
                relative=False,
            )
        except ConfigError:
            raise
        except Exception as exc:
            raise InfraError(f"构造官方 ScienceBoard Task 失败: {exc}") from exc

    def _official_eval(self, task: Any) -> bool:
        evaluate = getattr(task, "eval", None)
        if not callable(evaluate):
            raise InfraError("官方 Task 没有 eval()；拒绝重写打分逻辑")
        try:
            result = evaluate()
        except Exception as exc:
            raise InfraError(f"官方 evaluator 执行失败（不得记成模型 0 分）: {exc}") from exc
        if isinstance(result, bool):
            return result
        if result in (0, 1, 0.0, 1.0):
            return bool(result)
        raise InfraError(f"官方 evaluator 返回了无法解析的结果: {result!r}")

    def _step_loop(self, task: Any, agent: Harness, task_id: str, instruction: str) -> int:
        if self._experiment is None:
            raise ConfigError("ScienceBoardBench.run_trial 需要 Experiment（limits / protocol）")
        limits = self._experiment.agent.limits
        protocol = self._experiment.agent.protocol
        manager = getattr(task, "manager", None)
        if manager is None:
            raise InfraError("ScienceBoard Task 没有 manager")
        guest = ScienceBoardGuest(manager)
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
                guest.terminate(action.status)
                break
            if isinstance(action, ShellAction):
                guest.run_bash(action.command, action.timeout)
                continue
            try:
                guest._exec_gui(action)
            except ValueError:
                guest.terminate("fail")
                break
        return steps


__all__ = [
    "CLONE_HINT",
    "ScienceBoardBench",
    "collect_preflight",
    "default_root",
    "load_task_config",
    "require_ready",
    "task_json_path",
    "vm_path",
]
