"""自备 cordis.yml 的加载、护栏与运行期落地。

dsh 的 YAML 允许 `!!js` 表达式。PyYAML 默认会拒掉这个标签，doctor 和测试
必须用这里的 loader。运行时 adapter 会再 materialize 一份：把 MCP 的
command 写成当前解释器，避免 dsh 子进程找不到 `cua-eval-desktop-mcp`。
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from cua_eval.errors import ConfigError
from cua_eval.schema import Protocol

REQUIRED_PLUGINS = (
    "@deepseek-ai/dsh-sdk-jsonrpc-server",
    "@deepseek-ai/dsh-attachment-local",
    "@deepseek-ai/dsh-llm-pi-ai",
    "@deepseek-ai/dsh-mcp-client",
)

# 即使 guest_shell=true 也不得出现。guest 内的 shell 由本仓库 MCP 提供。
FORBIDDEN_PLUGINS = (
    "@deepseek-ai/dsh-bash-local",
    "@deepseek-ai/dsh-subprocess-local",
    "@deepseek-ai/dsh-fs-local",
    "@deepseek-ai/dsh-tool-bash-persistent",
)

# 默认统一走 pi-ai。直连 adapter 会把同一厂商挂两遍，且带专有 thinking 语义。
FORBIDDEN_LLM_PLUGINS = ("@deepseek-ai/dsh-llm-deepseek",)

_JS_ENV = re.compile(r"^process\.env\.([A-Z][A-Z0-9_]*)\s*\?\?\s*(.+)$")
MAX_IMAGE_DIMENSION = 2000
MAX_REQUEST_IMAGE_BYTES = 20 * 1024 * 1024


class _CordisLoader(yaml.SafeLoader):
    pass


def _construct_js(loader: yaml.SafeLoader, node: yaml.Node) -> str:
    if not isinstance(node, yaml.ScalarNode):
        raise TypeError("!!js expects a scalar")
    return str(loader.construct_scalar(node))


_CordisLoader.add_constructor("tag:yaml.org,2002:js", _construct_js)


def eval_js_expr(expr: str, environ: Mapping[str, str] | None = None) -> str:
    """解析 dsh 常用的 `process.env.X ?? 'default'`。其它表达式原样返回。"""
    env = environ if environ is not None else os.environ
    text = expr.strip()
    match = _JS_ENV.match(text)
    if not match:
        return text
    name, rest = match.group(1), match.group(2).strip()
    present = env.get(name)
    if present:
        return present
    if rest == "process.cwd()":
        return os.getcwd()
    if len(rest) >= 2 and rest[0] == rest[-1] and rest[0] in {"'", '"'}:
        return rest[1:-1]
    return rest


def _resolve_strings(node: object, environ: Mapping[str, str]) -> object:
    if isinstance(node, str):
        if "process.env." in node:
            return eval_js_expr(node, environ)
        return node
    if isinstance(node, list):
        return [_resolve_strings(item, environ) for item in node]
    if isinstance(node, dict):
        return {key: _resolve_strings(value, environ) for key, value in node.items()}
    return node


def load_cordis_yaml(
    path: Path,
    *,
    environ: Mapping[str, str] | None = None,
    resolve_js: bool = True,
) -> object:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"读不到 cordis 配置 {path}: {exc}") from exc
    try:
        raw = yaml.load(text, Loader=_CordisLoader)
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path} 不是合法的 YAML: {exc}") from exc
    if resolve_js:
        return _resolve_strings(raw, environ if environ is not None else os.environ)
    return raw


def plugin_names(raw: object) -> list[str]:
    names: list[str] = []
    if not isinstance(raw, list):
        return names
    for item in raw:
        if isinstance(item, dict):
            name = item.get("name")
            if isinstance(name, str):
                names.append(name)
    return names


def iter_plugins(raw: object) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def assert_osworld_cordis_guards(raw: object) -> None:
    """T4.3 护栏：禁挂宿主 bash/fs，必需插件都在，截图 route 声明了 image。"""
    names = plugin_names(raw)
    missing = [item for item in REQUIRED_PLUGINS if item not in names]
    if missing:
        raise ConfigError(f"cordis.yml 缺少必需插件: {', '.join(missing)}")
    forbidden = [item for item in (*FORBIDDEN_PLUGINS, *FORBIDDEN_LLM_PLUGINS) if item in names]
    if forbidden:
        raise ConfigError(
            "cordis.yml 挂了禁止的宿主侧插件: "
            + ", ".join(forbidden)
            + "。guest_shell=true 也不许挂 dsh-bash-local；shell 由 desktop MCP 提供。"
        )
    attachment = next(
        (p for p in iter_plugins(raw) if p.get("name") == "@deepseek-ai/dsh-attachment-local"),
        None,
    )
    if attachment is None:
        raise ConfigError("cordis.yml 缺少 dsh-attachment-local")
    raw_cfg = attachment.get("config")
    att_cfg: dict[str, Any] = raw_cfg if isinstance(raw_cfg, dict) else {}
    dimension = att_cfg.get("maxImageDimension")
    if dimension != MAX_IMAGE_DIMENSION:
        raise ConfigError(
            f"dsh-attachment-local.maxImageDimension 必须显式为 "
            f"{MAX_IMAGE_DIMENSION}，得到 {dimension!r}"
        )

    for route in ("vlm-cloud", "vlm-local"):
        if not _route_declares_image(raw, route):
            raise ConfigError(
                f"截图协议 route {route} 未声明 image（defaultInput 或 models[].input）。"
                "漏了截图会在附加前被拒。"
            )
        _assert_route_image_budget(raw, route)

    if _route_declares_image(raw, "text-cloud"):
        raise ConfigError("纯文本 route text-cloud 不得声明 image")

    spine = next(
        (p for p in iter_plugins(raw) if p.get("name") == "@deepseek-ai/dsh-agent-spine-demo"),
        None,
    )
    if spine is not None:
        raw_spine = spine.get("config")
        cfg: dict[str, Any] = raw_spine if isinstance(raw_spine, dict) else {}
        if cfg.get("toolBash") is not False:
            raise ConfigError("agent-spine 必须 toolBash: false，不要把宿主 bash 交给模型")


def _providers(raw: object) -> dict[str, Any]:
    found: dict[str, Any] = {}

    def walk(node: object) -> None:
        if isinstance(node, dict):
            providers = node.get("providers")
            if isinstance(providers, dict):
                for name, spec in providers.items():
                    if isinstance(name, str) and isinstance(spec, dict):
                        found[name] = spec
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(raw)
    return found


def _route_declares_image(raw: object, route: str) -> bool:
    spec = _providers(raw).get(route)
    if not isinstance(spec, dict):
        return False
    default = spec.get("defaultInput") or spec.get("default_input") or []
    if isinstance(default, list) and "image" in default:
        return True
    models = spec.get("models") or []
    if isinstance(models, list):
        for model in models:
            if not isinstance(model, dict):
                continue
            inputs = model.get("input") or model.get("inputModalities") or []
            if isinstance(inputs, list) and "image" in inputs:
                return True
    return False


def _assert_route_image_budget(raw: object, route: str) -> None:
    spec = _providers(raw).get(route)
    if not isinstance(spec, dict):
        raise ConfigError(f"找不到 route {route}")
    budget = spec.get("maxRequestImageBytes")
    if budget != MAX_REQUEST_IMAGE_BYTES:
        raise ConfigError(
            f"route {route} 必须显式写出 maxRequestImageBytes={MAX_REQUEST_IMAGE_BYTES}，"
            f"得到 {budget!r}"
        )


def materialize_cordis(
    src: Path,
    dest: Path,
    *,
    protocol: Protocol,
    max_screenshot_history: int,
    python_exe: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> Path:
    """写出一份给当前进程用的 cordis：MCP command 指向本解释器。"""
    env = dict(environ if environ is not None else os.environ)
    env.setdefault("CUA_EVAL_MCP_OBSERVATION", protocol.observation.value)
    env.setdefault("CUA_EVAL_MCP_GUEST_ACTIONS", protocol.guest_actions.value)
    env.setdefault("CUA_EVAL_MCP_GUEST_SHELL", "true" if protocol.guest_shell else "false")
    env.setdefault("CUA_EVAL_MCP_MAX_SCREENSHOT_HISTORY", str(max_screenshot_history))
    env.setdefault("CUA_EVAL_MCP_BACKEND", "fake")
    raw = load_cordis_yaml(src, environ=env)
    exe = python_exe or sys.executable
    for plugin in iter_plugins(raw):
        if plugin.get("name") != "@deepseek-ai/dsh-mcp-client":
            continue
        cfg = plugin.setdefault("config", {})
        if not isinstance(cfg, dict):
            raise ConfigError("mcp-client config 必须是映射")
        cfg["command"] = exe
        cfg["args"] = ["-m", "cua_eval.harness.desktop_mcp"]
        plugin_env = cfg.setdefault("env", {})
        if not isinstance(plugin_env, dict):
            raise ConfigError("mcp-client env 必须是映射")
        for key in (
            "CUA_EVAL_MCP_OBSERVATION",
            "CUA_EVAL_MCP_GUEST_ACTIONS",
            "CUA_EVAL_MCP_GUEST_SHELL",
            "CUA_EVAL_MCP_MAX_SCREENSHOT_HISTORY",
            "CUA_EVAL_MCP_BACKEND",
            "CUA_EVAL_MCP_GUEST_HOSTNAME",
            "CUA_EVAL_MCP_ACTION_LOG",
            "CUA_EVAL_MCP_NATIVE_WIDTH",
            "CUA_EVAL_MCP_NATIVE_HEIGHT",
        ):
            if key in env:
                plugin_env[key] = env[key]
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        yaml.safe_dump(raw, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return dest


__all__ = [
    "FORBIDDEN_LLM_PLUGINS",
    "FORBIDDEN_PLUGINS",
    "MAX_IMAGE_DIMENSION",
    "MAX_REQUEST_IMAGE_BYTES",
    "REQUIRED_PLUGINS",
    "assert_osworld_cordis_guards",
    "eval_js_expr",
    "load_cordis_yaml",
    "materialize_cordis",
    "plugin_names",
]
