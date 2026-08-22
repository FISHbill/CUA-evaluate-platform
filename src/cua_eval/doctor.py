"""主机与模型端点探活。缺项退出非 0，绝不把 dummy 说成已验证模型。"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from cua_eval.errors import ConfigError
from cua_eval.harness.cordis import REQUIRED_PLUGINS, load_cordis_yaml
from cua_eval.schema import OSWORLD_MIN_COMMIT, Experiment, ModelBackend, Observation

OSWORLD_MIN_VCPU = 8
OSWORLD_MIN_RAM_GB = 32.0
OSWORLD_MIN_DISK_GB = 150.0


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class HostFacts:
    python: tuple[int, int]
    uv_path: str | None
    docker_path: str | None
    kvm_readable: bool
    cpu_count: int | None
    ram_gb: float | None
    disk_free_gb: float | None


@dataclass(frozen=True)
class DoctorReport:
    checks: tuple[CheckResult, ...]
    ok: bool

    def render(self) -> str:
        width = max((len(c.name) for c in self.checks), default=8)
        lines = ["cua-eval doctor"]
        for check in self.checks:
            flag = "OK  " if check.ok else "FAIL"
            lines.append(f"  {check.name:<{width}}  {flag}  {check.detail}")
        failed = [c.name for c in self.checks if not c.ok]
        lines.append("")
        if failed:
            lines.append("缺项: " + ", ".join(failed))
            lines.append(
                "此机器还不能跑 OSWorld 真题。阶段 0 假评测（dummy + fake）不依赖 Docker/KVM。"
            )
            lines.append("不得把 dummy 分数当成已验证模型，缺端点时也不要退化成 dummy。")
        else:
            lines.append("主机检查通过。")
        return "\n".join(lines)


def collect_host_facts() -> HostFacts:
    return HostFacts(
        python=(sys.version_info.major, sys.version_info.minor),
        uv_path=shutil.which("uv"),
        docker_path=shutil.which("docker"),
        kvm_readable=_kvm_readable(),
        cpu_count=os.cpu_count(),
        ram_gb=_ram_gb(),
        disk_free_gb=_disk_free_gb(),
    )


def _kvm_readable() -> bool:
    path = Path("/dev/kvm")
    return path.exists() and os.access(path, os.R_OK)


def _ram_gb() -> float | None:
    meminfo = Path("/proc/meminfo")
    try:
        text = meminfo.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        if line.startswith("MemTotal:"):
            parts = line.split()
            return int(parts[1]) / 1024 / 1024
    return None


def _disk_free_gb() -> float | None:
    try:
        usage = shutil.disk_usage("/")
    except OSError:
        return None
    return usage.free / (1024**3)


def evaluate_host(facts: HostFacts) -> list[CheckResult]:
    python_ok = facts.python >= (3, 11)
    checks = [
        CheckResult(
            name="python",
            ok=python_ok,
            detail=f"{facts.python[0]}.{facts.python[1]}（需要 >= 3.11）",
        ),
        CheckResult(
            name="uv",
            ok=facts.uv_path is not None,
            detail=facts.uv_path or "未在 PATH 中找到 uv",
        ),
        CheckResult(
            name="docker",
            ok=facts.docker_path is not None,
            detail=facts.docker_path or "未安装。OSWorld 真跑需要 Docker。",
        ),
        CheckResult(
            name="/dev/kvm",
            ok=facts.kvm_readable,
            detail=(
                "当前用户可读"
                if facts.kvm_readable
                else "不存在或当前用户不可读。无 KVM 时桌面 VM 会慢一个数量级，不适合作为主路径。"
            ),
        ),
    ]
    cpu = facts.cpu_count
    checks.append(
        CheckResult(
            name="cpu",
            ok=cpu is not None and cpu >= OSWORLD_MIN_VCPU,
            detail=(
                f"{cpu} vCPU（OSWorld smoke 需要 >= {OSWORLD_MIN_VCPU}）"
                if cpu is not None
                else f"读不到 CPU 数量（OSWorld smoke 需要 >= {OSWORLD_MIN_VCPU}）"
            ),
        )
    )
    ram = facts.ram_gb
    checks.append(
        CheckResult(
            name="memory",
            ok=ram is not None and ram >= OSWORLD_MIN_RAM_GB,
            detail=(
                f"{ram:.1f} GB（OSWorld smoke 需要 >= {OSWORLD_MIN_RAM_GB:.0f} GB）"
                if ram is not None
                else f"读不到内存（OSWorld smoke 需要 >= {OSWORLD_MIN_RAM_GB:.0f} GB）"
            ),
        )
    )
    disk = facts.disk_free_gb
    checks.append(
        CheckResult(
            name="disk",
            ok=disk is not None and disk >= OSWORLD_MIN_DISK_GB,
            detail=(
                f"{disk:.0f} GB free（OSWorld smoke 建议根盘空闲 >= {OSWORLD_MIN_DISK_GB:.0f} GB）"
                if disk is not None
                else f"读不到磁盘（OSWorld smoke 建议根盘空闲 >= {OSWORLD_MIN_DISK_GB:.0f} GB）"
            ),
        )
    )
    return checks


def _providers_from_cordis(raw: object) -> dict[str, Any]:
    found: dict[str, Any] = {}

    def walk(node: object) -> None:
        if isinstance(node, Mapping):
            providers = node.get("providers")
            if isinstance(providers, Mapping):
                for name, spec in providers.items():
                    if isinstance(name, str) and isinstance(spec, Mapping):
                        found[name] = spec
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(raw)
    return found


def route_declares_image(cordis_raw: object, route_name: str) -> bool:
    """route 级 `defaultInput` 或某个模型的 `input` 含 image 即视为声明了视觉能力。"""
    providers = _providers_from_cordis(cordis_raw)
    spec = providers.get(route_name)
    if not isinstance(spec, Mapping):
        return False
    default = spec.get("defaultInput") or spec.get("default_input") or []
    if isinstance(default, list) and "image" in default:
        return True
    models = spec.get("models") or []
    if isinstance(models, list):
        for model in models:
            if not isinstance(model, Mapping):
                continue
            inputs = model.get("input") or model.get("inputModalities") or []
            if isinstance(inputs, list) and "image" in inputs:
                return True
    return False


def cordis_base_url(cordis_raw: object, route_name: str) -> str | None:
    spec = _providers_from_cordis(cordis_raw).get(route_name)
    if not isinstance(spec, Mapping):
        return None
    url = spec.get("baseURL") or spec.get("base_url")
    if not isinstance(url, str) or not url.strip():
        return None
    stripped = url.strip()
    if stripped.startswith("<") and stripped.endswith(">"):
        return None
    return stripped


def probe_openai_compat(base_url: str, timeout_seconds: float = 2.0) -> CheckResult:
    url = base_url.rstrip("/") + "/models"
    try:
        request = Request(url, method="GET")
        with urlopen(request, timeout=timeout_seconds) as response:
            status = getattr(response, "status", 200)
            if 200 <= int(status) < 500:
                return CheckResult(name="endpoint", ok=True, detail=f"{url} HTTP {status}")
            return CheckResult(name="endpoint", ok=False, detail=f"{url} HTTP {status}")
    except (URLError, TimeoutError, OSError) as exc:
        return CheckResult(
            name="endpoint",
            ok=False,
            detail=f"端点不可达 ({url}): {exc}。不要退化成 dummy。",
        )


_DSH_INIT_PAYLOAD = (
    '{"jsonrpc":"2.0","id":"1","method":"initialize",'
    '"params":{"cwd":"/tmp","provider":"vlm-cloud","model":"openai-compat-vlm"}}\n'
)
_DSH_JSONRPC_SERVER = "@deepseek-ai/dsh-sdk-jsonrpc-server"


def _jsonrpc_initialize(
    binary: Path,
    cordis_path: Path,
    *,
    environ: Mapping[str, str],
    timeout_seconds: float,
) -> subprocess.CompletedProcess[str]:
    env = dict(environ)
    env["DSH_CORDIS_CONFIG"] = str(cordis_path)
    return subprocess.run(
        [str(binary)],
        input=_DSH_INIT_PAYLOAD,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        env=env,
        check=False,
    )


def _is_plugin_import_error(stderr: str, plugin: str) -> bool:
    """True when the snapshot cannot import ``plugin`` at all."""
    if f"Cannot find package '{plugin}'" in stderr:
        return True
    return "failed to import loader entry" in stderr and plugin in stderr


def _plugins_missing_from_runtime(
    binary: Path,
    *,
    timeout_seconds: float,
) -> list[str]:
    """Probe each required plugin *alone*.

    Loading the full OSWorld cordis with several missing plugins collapses
    stderr to a nameless ``AggregateError``. Adding one plugin on top of the
    jsonrpc-server entry names the missing package.
    """
    missing: list[str] = []
    for plugin in REQUIRED_PLUGINS:
        if plugin == _DSH_JSONRPC_SERVER:
            continue
        with tempfile.TemporaryDirectory(prefix="cua-eval-dsh-") as tmp:
            path = Path(tmp) / "cordis.yml"
            path.write_text(
                "- id: sdk-jsonrpc-server\n"
                f"  name: '{_DSH_JSONRPC_SERVER}'\n"
                "- id: probe\n"
                f"  name: '{plugin}'\n",
                encoding="utf-8",
            )
            try:
                proc = _jsonrpc_initialize(
                    binary,
                    path,
                    environ=os.environ,
                    timeout_seconds=timeout_seconds,
                )
            except (subprocess.TimeoutExpired, OSError):
                missing.append(plugin)
                continue
            if _is_plugin_import_error(proc.stderr or "", plugin):
                missing.append(plugin)
    return missing


def probe_dsh_cordis(
    cordis_path: Path,
    *,
    environ: Mapping[str, str] | None = None,
    timeout_seconds: float = 6.0,
) -> CheckResult:
    """jsonrpc-agent 能否加载这份 cordis。缺 mcp-client / attachment 时必须说清楚。"""
    try:
        from deepseek_harness_runtime import bundled_runtime_path
    except ImportError:
        return CheckResult(
            name="dsh_runtime",
            ok=False,
            detail="未安装 deepseek-harness-runtime-bin。不要退化成 dummy。",
        )
    try:
        binary = bundled_runtime_path()
    except FileNotFoundError as exc:
        return CheckResult(name="dsh_runtime", ok=False, detail=str(exc))

    missing = _plugins_missing_from_runtime(binary, timeout_seconds=timeout_seconds)
    if missing:
        return CheckResult(
            name="dsh_runtime",
            ok=False,
            detail=(
                "当前 deepseek-harness-runtime-bin 的 jsonrpc-agent 快照缺少: "
                + ", ".join(missing)
                + "。自备 cordis 仍必须挂它们（AGENTS.md §6.2）；"
                "换带完整插件集的 runtime 之前不要假装跑过。"
            ),
        )

    env = dict(environ if environ is not None else os.environ)
    try:
        proc = _jsonrpc_initialize(
            binary,
            cordis_path,
            environ=env,
            timeout_seconds=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return CheckResult(
            name="dsh_runtime",
            ok=False,
            detail="dsh-jsonrpc-agent initialize 超时。不要退化成 dummy。",
        )
    except OSError as exc:
        return CheckResult(
            name="dsh_runtime",
            ok=False,
            detail=f"无法启动 dsh-jsonrpc-agent: {exc}。不要退化成 dummy。",
        )
    stderr = proc.stderr or ""
    loaded = "plugin tree failed to load" not in stderr and bool((proc.stdout or "").strip())
    if loaded:
        return CheckResult(name="dsh_runtime", ok=True, detail=str(binary))
    detail = stderr.strip().splitlines()[0] if stderr.strip() else "initialize 失败"
    return CheckResult(name="dsh_runtime", ok=False, detail=detail[:300])


def evaluate_experiment(
    experiment: Experiment,
    *,
    environ: Mapping[str, str] | None = None,
    probe: bool = True,
) -> list[CheckResult]:
    env = environ if environ is not None else os.environ
    checks: list[CheckResult] = []
    model = experiment.agent.model

    if experiment.bench.value == "osworld_verified":
        pin = experiment.bench_version or ""
        checks.append(
            CheckResult(
                name="bench_pin",
                ok=bool(pin),
                detail=(
                    f"{pin}（下限 {OSWORLD_MIN_COMMIT}）"
                    if pin
                    else f"osworld_verified 必须 pin commit（下限 {OSWORLD_MIN_COMMIT}）"
                ),
            )
        )
        from cua_eval.benches.osworld import collect_preflight, default_osworld_root

        for item in collect_preflight(default_osworld_root(), pin or OSWORLD_MIN_COMMIT):
            if item.name == "/dev/kvm":
                continue
            checks.append(CheckResult(name=item.name, ok=item.ok, detail=item.detail))

    if model.backend is ModelBackend.DUMMY:
        checks.append(
            CheckResult(
                name="model",
                ok=True,
                detail="backend=dummy，仅平台自测，不是推理验证，不能当成已验证模型。",
            )
        )
        return checks

    if experiment.agent.protocol.observation is Observation.SCREENSHOT and not model.accepts_images:
        checks.append(
            CheckResult(
                name="image_modality",
                ok=False,
                detail="observation=screenshot 但 model.input_modalities 未含 image。",
            )
        )
    elif experiment.agent.protocol.observation is Observation.SCREENSHOT:
        checks.append(
            CheckResult(
                name="image_modality",
                ok=True,
                detail="model.input_modalities 已声明 image。",
            )
        )

    key_name = model.api_key_env
    if key_name:
        present = bool(env.get(key_name))
        checks.append(
            CheckResult(
                name="api_key",
                ok=present,
                detail=(
                    f"环境变量 {key_name} 已设置"
                    if present
                    else (
                        f"环境变量 {key_name} 未设置。"
                        "密钥只从环境变量读，未配置时不要退化成 dummy。"
                    )
                ),
            )
        )
    else:
        checks.append(
            CheckResult(
                name="api_key",
                ok=False,
                detail="openai_compat 需要 api_key_env。端点未配置，不要退化成 dummy。",
            )
        )

    cordis = experiment.agent.cordis_config
    route = model.provider_route
    if cordis is None:
        checks.append(
            CheckResult(
                name="cordis",
                ok=False,
                detail="未指定 cordis_config。禁止使用 dsh 零配置默认组合。",
            )
        )
        return checks

    cordis_path = Path(cordis)
    if not cordis_path.is_file():
        checks.append(
            CheckResult(
                name="cordis",
                ok=False,
                detail=f"{cordis_path} 不存在。端点未配置，不要退化成 dummy。",
            )
        )
        return checks

    try:
        raw = load_cordis_yaml(cordis_path, environ=env)
    except ConfigError as exc:
        checks.append(CheckResult(name="cordis", ok=False, detail=str(exc)))
        return checks

    checks.append(CheckResult(name="cordis", ok=True, detail=str(cordis_path)))

    if route and experiment.agent.protocol.observation is Observation.SCREENSHOT:
        has_image = route_declares_image(raw, route)
        checks.append(
            CheckResult(
                name="route_image",
                ok=has_image,
                detail=(
                    f"route {route} 已声明 image"
                    if has_image
                    else (
                        f"纯文本 route {route} 不能跑 observation=screenshot。"
                        "漏声明则截图在附加前就会被拒。"
                    )
                ),
            )
        )

    if route:
        base_url = cordis_base_url(raw, route)
        if not base_url:
            checks.append(
                CheckResult(
                    name="endpoint",
                    ok=False,
                    detail="端点未配置：cordis.yml 里没有可用的 baseURL。不要退化成 dummy。",
                )
            )
        elif probe:
            checks.append(probe_openai_compat(base_url))
        else:
            checks.append(
                CheckResult(name="endpoint", ok=True, detail=f"baseURL={base_url}（未探测）")
            )

    if probe:
        checks.append(probe_dsh_cordis(cordis_path, environ=env))
    return checks


def run_doctor(
    experiment: Experiment | None,
    *,
    facts: HostFacts | None = None,
    environ: Mapping[str, str] | None = None,
    probe: bool = True,
) -> DoctorReport:
    checks = evaluate_host(facts or collect_host_facts())
    if experiment is not None:
        checks.extend(evaluate_experiment(experiment, environ=environ, probe=probe))
    ok = all(c.ok for c in checks)
    return DoctorReport(checks=tuple(checks), ok=ok)
