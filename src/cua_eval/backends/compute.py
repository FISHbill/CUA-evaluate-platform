"""评测作业宿主机。本阶段只有 `local_linux` 会真正执行。"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from cua_eval.errors import UnsupportedComputeBackend
from cua_eval.schema import ComputeBackend

T = TypeVar("T")


def run_job(backend: ComputeBackend, job: Callable[[], T]) -> T:
    """在指定计算后端上跑一个作业。

    `local_linux` 就是当前进程。其余枚举值必须 raise，不能静默落到本机——
    否则会把「还没实现的 Windows / 集群」伪装成已经跑过。
    """
    if backend is ComputeBackend.LOCAL_LINUX:
        return job()
    raise UnsupportedComputeBackend(
        f"compute_backend={backend.value} 本阶段未实现，仅 local_linux 可运行"
    )


__all__ = ["run_job"]
