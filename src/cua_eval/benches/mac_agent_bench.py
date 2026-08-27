"""MacAgentBench adapter：包装官方 `MacOSEnv.evaluate_task()`。

不把官方源码复制进本仓库；checkout 到 `third_party/MacAgentBench`。
**不会**下载 HDD / 拉 Docker-OSX 镜像。缺 checkout 或远程沙箱就在 prepare() 失败。
通用 `macos` bench 仍是 UnsupportedBenchError；本 adapter 的 id 是 `mac_agent_bench`。

桌面落点是远程 macOS guest（lucwei Fleet 截图 + SSH 键鼠），不是评测宿主机。
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from cua_eval.actions import ShellAction, TerminateAction, to_pyautogui, validate_against_protocol
from cua_eval.benches.base import RawResult
from cua_eval.benches.gitpin import Prereq, checkout_prereq, ensure_on_path, third_party_root
from cua_eval.benches.lucwei_mac import (
    FLEET_POOL_ENV,
    FLEET_URL_ENV,
    FLEET_UUID_ENV,
    LucweiMacGuest,
    fetch_screenshot_png,
    fleet_pool,
    fleet_url,
    fleet_vm_uuid,
    http_get,
    screenshot_url,
)
from cua_eval.errors import ConfigError, InfraError
from cua_eval.harness.base import Harness, StepObservation
from cua_eval.schema import Experiment, FailureClass, Observation, TrialResult

CLONE_HINT = "git clone https://github.com/JetAstra/MacAgentBench.git third_party/MacAgentBench"
ROOT_ENV = "CUA_EVAL_MACAGENTBENCH_ROOT"


def default_root() -> Path:
    return third_party_root("MacAgentBench", ROOT_ENV)


def task_json_path(root: Path, task_id: str) -> Path | None:
    tasks = root / "tasks"
    if not tasks.is_dir():
        return None
    relative = Path(*task_id.split("/")).with_suffix(".json")
    candidate = tasks / relative
    if candidate.is_file():
        return candidate
    matches = sorted(tasks.glob(f"**/{Path(task_id).name}.json"))
    return matches[0] if matches else None


def collect_preflight(
    root: Path,
    pin: str,
    *,
    environ: dict[str, str] | None = None,
    probe: bool = False,
) -> list[Prereq]:
    env = environ if environ is not None else dict(os.environ)
    checks = checkout_prereq(
        root, pin=pin, clone_hint=CLONE_HINT, check_name="mac_agent_bench_checkout"
    )
    hdd = env.get("MAC_AGENT_BENCH_MAC_HDD_IMG_PATH", "").strip()
    checks.append(
        Prereq(
            "mac_agent_bench_hdd",
            True,
            (
                f"本机 HDD 路径已设置（{hdd}）；远程 Fleet 路径仍优先，adapter 不会下载镜像。"
                if hdd
                else "未设置 MAC_AGENT_BENCH_MAC_HDD_IMG_PATH。本路径使用远程 macOS 沙箱，"
                "不要求本机 Docker-OSX / HDD；adapter 禁止下载镜像。"
            ),
        )
    )

    base = fleet_url(env)
    pool = fleet_pool(env)
    vm_uuid = fleet_vm_uuid(env)
    if not base:
        checks.append(
            Prereq(
                "mac_fleet_url",
                False,
                f"未设置 {FLEET_URL_ENV}。远程 Mac 沙箱控制面只从环境变量读，"
                "不要写进 git，也不要退化成 dummy。",
            )
        )
    else:
        parsed = urlparse(base)
        ok = parsed.scheme in {"http", "https"} and bool(parsed.netloc)
        checks.append(
            Prereq(
                "mac_fleet_url",
                ok,
                base if ok else f"{FLEET_URL_ENV} 不是合法 URL: {base}",
            )
        )
    checks.append(
        Prereq(
            "mac_fleet_pool",
            bool(pool),
            pool if pool else f"未设置 {FLEET_POOL_ENV}。池 id 不是口令，乱写会 unknown access label。",
        )
    )
    checks.append(
        Prereq(
            "mac_fleet_vm",
            bool(vm_uuid),
            vm_uuid if vm_uuid else f"未设置 {FLEET_UUID_ENV}。doctor 需要能定位要拉图的 VM。",
        )
    )
    if probe and base and pool and vm_uuid:
        try:
            png, width, height = fetch_screenshot_png(
                base_url=base, pool=pool, vm_uuid=vm_uuid, getter=http_get
            )
            checks.append(
                Prereq(
                    "mac_fleet_screenshot",
                    bool(png),
                    f"{screenshot_url(base, vm_uuid)} {width}x{height}",
                )
            )
        except (InfraError, ConfigError, OSError) as exc:
            checks.append(Prereq("mac_fleet_screenshot", False, str(exc)))
    ssh_host = env.get("CUA_EVAL_MAC_SSH_HOST", "").strip()
    ssh_user = env.get("CUA_EVAL_MAC_SSH_USER", "").strip()
    checks.append(
        Prereq(
            "mac_ssh",
            bool(ssh_host and ssh_user),
            (
                f"{ssh_user}@{ssh_host}"
                if ssh_host and ssh_user
                else "未设置 CUA_EVAL_MAC_SSH_HOST / CUA_EVAL_MAC_SSH_USER。"
                "键鼠与 guest shell 经 SSH 落到 Mac 客户机，不是评测宿主机。"
            ),
        )
    )
    return checks


def require_ready(root: Path, pin: str, *, environ: dict[str, str] | None = None) -> None:
    failed = [item for item in collect_preflight(root, pin, environ=environ, probe=False) if not item.ok]
    if not failed:
        return
    lines = "; ".join(f"{item.name}: {item.detail}" for item in failed)
    raise ConfigError(
        "MacAgentBench 前置检查未通过（不会退化成 dummy，也不会下载 HDD）：" + lines
    )


def load_task_config(root: Path, task_id: str) -> dict[str, Any]:
    path = task_json_path(root, task_id)
    if path is None:
        raise ConfigError(f"在 {root}/tasks 下找不到任务 {task_id}.json")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"读任务 JSON {path} 失败: {exc}") from exc
    if not isinstance(payload, dict):
        raise ConfigError(f"{path} 的顶层必须是对象")
    return payload


def coerce_official_score(result: object) -> float:
    if result is None:
        raise InfraError("官方 evaluator 没有返回结果")
    score = getattr(result, "score", None)
    if isinstance(score, int | float):
        value = float(score)
    elif isinstance(result, bool | int | float):
        value = float(result)
    else:
        try:
            value = float(result)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise InfraError(f"官方 evaluator 返回了无法解析的分数: {result!r}") from exc
    if value < 0.0 or value > 1.0:
        # 官方 bool 已经是 0/1；若给出百分数则视为环境异常，避免污染口径。
        if value == 100.0:
            return 1.0
        raise InfraError(f"官方 evaluator 分数超出 0.0–1.0: {value}")
    return value


def _score_class(score: float) -> FailureClass:
    return FailureClass.OK if score >= 1.0 else FailureClass.TASK_FAIL


class MacAgentBench:
    """官方 MacOSEnv.evaluate_task 的薄包装。prepare 只做前置检查。"""

    id = "mac_agent_bench"

    def __init__(
        self,
        experiment: Experiment | None = None,
        *,
        root: Path | None = None,
        env_factory: Callable[[], Any] | None = None,
        skip_host_preflight: bool = False,
        environ: dict[str, str] | None = None,
    ) -> None:
        self._experiment = experiment
        self._root = Path(root) if root is not None else default_root()
        self._env_factory = env_factory
        self._skip_host_preflight = skip_host_preflight
        self._environ = environ
        self._env: Any = None

    def _env_map(self) -> dict[str, str]:
        return dict(self._environ) if self._environ is not None else dict(os.environ)

    def _pin(self) -> str:
        if self._experiment is not None and self._experiment.bench_version:
            return self._experiment.bench_version
        return "65632d1"

    def prepare(self) -> None:
        if os.environ.get("CUA_EVAL_MACAGENTBENCH_ALLOW_DOWNLOAD") == "1":
            raise ConfigError(
                "adapter 禁止自动下载 Mac HDD / Docker-OSX 镜像，即使设置了 "
                "CUA_EVAL_MACAGENTBENCH_ALLOW_DOWNLOAD。"
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
        task = load_task_config(self._root, task_id)
        instruction = str(task.get("instruction") or f"MacAgentBench task {task_id}")
        path = task_json_path(self._root, task_id)
        if path is None:
            raise ConfigError(f"找不到任务 JSON: {task_id}")
        try:
            env = self._ensure_env()
            init = getattr(env, "init_task", None)
            if callable(init):
                init(str(path))
        except ConfigError:
            raise
        except Exception as exc:
            raise InfraError(
                f"MacAgentBench init_task 失败（环境故障，不得记成模型 0 分）: {exc}"
            ) from exc

        extra_env = {
            "CUA_EVAL_MCP_BACKEND": "lucwei_mac",
            "CUA_EVAL_MACAGENTBENCH_ROOT": str(self._root),
            **{
                key: value
                for key, value in self._env_map().items()
                if key.startswith("CUA_EVAL_MAC_")
            },
        }
        run_task = getattr(agent, "run_task", None)
        try:
            if callable(run_task):
                work_dir = Path(tempfile.mkdtemp(prefix="cua-eval-mac-"))
                try:
                    run_task(instruction, work_dir=work_dir, extra_env=extra_env)
                finally:
                    import shutil

                    shutil.rmtree(work_dir, ignore_errors=True)
                steps = int(getattr(env, "_step_no", 0) or getattr(getattr(env, "task", None), "step_no", 0) or 0)
            else:
                steps = self._step_loop(env, agent, task_id, instruction)
            score = self._official_score(env)
        except (InfraError, ConfigError):
            raise
        except Exception as exc:
            raise InfraError(f"MacAgentBench trial 失败（环境故障，不得记成模型 0 分）: {exc}") from exc

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
        if env is None:
            return
        closer = getattr(env, "close_connection", None) or getattr(env, "close", None)
        if callable(closer):
            try:
                closer()
            except Exception:
                return

    def _ensure_env(self) -> Any:
        if self._env is not None:
            return self._env
        if self._env_factory is not None:
            self._env = self._env_factory()
            return self._env
        self._env = self._load_official_env()
        return self._env

    def _load_official_env(self) -> Any:
        ensure_on_path(self._root)
        env_map = self._env_map()
        host = env_map.get("CUA_EVAL_MAC_SSH_HOST", "").strip()
        user = env_map.get("CUA_EVAL_MAC_SSH_USER", "").strip()
        if not host or not user:
            raise ConfigError(
                "构造官方 MacOSEnv 需要 CUA_EVAL_MAC_SSH_HOST 与 CUA_EVAL_MAC_SSH_USER。"
                "adapter 不会在本机拉 Docker-OSX。"
            )
        try:
            from controllers.env import MacOSEnv
        except ImportError as exc:
            raise ConfigError(
                f"导入不了 controllers.env（root={self._root}）。"
                "请 checkout 官方 MacAgentBench 到 third_party/MacAgentBench。"
            ) from exc
        port_raw = env_map.get("CUA_EVAL_MAC_SSH_PORT", "22").strip() or "22"
        password_env = env_map.get("CUA_EVAL_MAC_SSH_PASSWORD_ENV", "").strip()
        password = env_map.get(password_env, "") if password_env else ""
        config_path = Path(tempfile.mkdtemp(prefix="cua-eval-mac-cfg-")) / "remote.yml"
        payload = {
            "mode": "remote",
            "host_ip": host,
            "port": int(port_raw),
            "username": user,
            "password": password,
            "action_space": "pyautogui",
        }
        config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
        try:
            env = MacOSEnv(config_file=str(config_path))
            env.connect_ssh()
        except ConfigError:
            raise
        except Exception as exc:
            raise InfraError(f"连接远程 Mac guest 失败（不得记成模型 0 分）: {exc}") from exc
        return env

    def _official_score(self, env: Any) -> float:
        evaluate = getattr(env, "evaluate_task", None)
        if not callable(evaluate):
            raise InfraError("官方 MacOSEnv 没有 evaluate_task；拒绝重写打分逻辑")
        try:
            result = evaluate()
        except Exception as exc:
            raise InfraError(f"官方 evaluator 执行失败（不得记成模型 0 分）: {exc}") from exc
        return coerce_official_score(result)

    def _step_loop(self, env: Any, agent: Harness, task_id: str, instruction: str) -> int:
        if self._experiment is None:
            raise ConfigError("MacAgentBench.run_trial 需要 Experiment（limits / protocol）")
        limits = self._experiment.agent.limits
        protocol = self._experiment.agent.protocol
        guest = self._guest_for_env(env)
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
                stepper = getattr(env, "step", None)
                if callable(stepper):
                    try:
                        stepper(done_token, pause=0)
                    except Exception:
                        guest.terminate(action.status)
                else:
                    guest.terminate(action.status)
                break
            if isinstance(action, ShellAction):
                guest.run_bash(action.command, action.timeout)
                continue
            stepper = getattr(env, "step", None)
            if callable(stepper):
                try:
                    stepper(to_pyautogui(action), pause=0.5)
                    continue
                except ValueError:
                    guest.terminate("fail")
                    break
            try:
                self._apply_gui(guest, action)
            except ValueError:
                guest.terminate("fail")
                break
        return steps

    def _apply_gui(self, guest: Any, action: object) -> None:
        if hasattr(guest, "_exec_gui"):
            guest._exec_gui(action)
            return
        raise InfraError("MacAgentBench guest 无法执行键鼠")

    def _guest_for_env(self, env: Any) -> Any:
        capture = getattr(env, "capture_png", None)
        if callable(capture):
            return env
        return LucweiMacGuest.from_env(self._env_map())


__all__ = [
    "CLONE_HINT",
    "MacAgentBench",
    "Prereq",
    "coerce_official_score",
    "collect_preflight",
    "default_root",
    "load_task_config",
    "require_ready",
    "task_json_path",
]
