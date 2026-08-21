"""平台异常体系。

每个异常都能映射到 AGENTS.md 第 4 节的失败类，这样编排器捕获异常后无需再猜该
把这次 trial 记成什么。映射由 `failure_class` 属性给出；`ConfigError` 之类在跑
题之前就该拦住的错误没有对应失败类，映射为 `None`。
"""

from __future__ import annotations


class CuaEvalError(Exception):
    """本平台所有异常的基类。

    `failure_class` 是该异常对应的 `FailureClass` 值（用字符串避免 schema 与
    errors 相互 import）。`None` 表示这类错误不该被记成某次 trial 的结果。
    """

    failure_class: str | None = None


class ConfigError(CuaEvalError):
    """实验配置本身不合法。应在跑题之前失败，不记成任何 trial 结果。"""


# 名字不带 Error 后缀是 AGENTS.md 第 3 节点名要求的，保持一致以便对照规格。
class UnsupportedComputeBackend(CuaEvalError):  # noqa: N818
    """请求了本阶段未实现的 `ComputeBackend`。"""


class UnsupportedBenchError(CuaEvalError):
    """请求了本平台不支持的 bench，例如 Linux 上的 macOS 客户机。"""


class InfraError(CuaEvalError):
    """环境侧失败：reset 失败、容器起不来、VNC 挂掉、429 等。

    **绝不能记成模型 0 分**，否则会污染 Table 1。
    """

    failure_class = "infra_error"


class HarnessError(InfraError):
    """harness 侧失败（dsh 起不来、MCP 通道断开）。按环境失败处理。"""


class ModelError(CuaEvalError):
    """模型自身的问题：反复给不出可解析的动作、超上下文等。计入失败。"""

    failure_class = "model_error"


__all__ = [
    "ConfigError",
    "CuaEvalError",
    "HarnessError",
    "InfraError",
    "ModelError",
    "UnsupportedBenchError",
    "UnsupportedComputeBackend",
]
