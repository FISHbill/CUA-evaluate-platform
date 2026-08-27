"""第三方 bench 仓库的 checkout / commit pin 核对。

adapter 不自动 clone、不下载镜像。HEAD 与配置 pin 不一致就失败。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


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


def third_party_root(name: str, env_var: str) -> Path:
    override = os.environ.get(env_var)
    if override:
        return Path(override)
    return package_repo_root() / "third_party" / name


def git_run(root: Path, *args: str, timeout: float = 8.0) -> subprocess.CompletedProcess[str]:
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


def checkout_prereq(root: Path, *, pin: str, clone_hint: str, check_name: str) -> list[Prereq]:
    if not root.is_dir():
        return [
            Prereq(
                check_name,
                False,
                f"{root} 不存在。请 {clone_hint} 并 checkout 到 pin {pin}。"
                "adapter 不会自动 clone，也不会下载镜像。不要退化成 dummy。",
            )
        ]
    if not (root / ".git").exists():
        return [
            Prereq(
                check_name,
                False,
                f"{root} 不是 git 仓库。请用官方 checkout，不要把源码复制进本仓库。",
            )
        ]
    return [Prereq(check_name, True, str(root)), verify_pin(root, pin, check_name=f"{check_name}_commit")]


def verify_pin(root: Path, pin: str, *, check_name: str) -> Prereq:
    if shutil.which("git") is None:
        return Prereq(check_name, False, "需要 git 核对 commit pin")
    try:
        head_p = git_run(root, "rev-parse", "HEAD")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return Prereq(check_name, False, f"读 HEAD 失败: {exc}")
    if head_p.returncode != 0:
        return Prereq(check_name, False, "无法读取 HEAD")
    head = head_p.stdout.strip()
    resolved_p = git_run(root, "rev-parse", "--verify", f"{pin}^{{commit}}")
    if resolved_p.returncode != 0:
        resolved_p = git_run(root, "rev-parse", "--verify", pin)
    if resolved_p.returncode != 0:
        return Prereq(check_name, False, f"checkout 里没有 pin {pin}")
    resolved = resolved_p.stdout.strip()
    if resolved != head:
        return Prereq(
            check_name,
            False,
            f"HEAD={head[:12]} 与配置 pin {pin} 不一致；不要浮动 main。",
        )
    return Prereq(check_name, True, head[:12])


def ensure_on_path(root: Path) -> None:
    import sys

    text = str(root)
    if text not in sys.path:
        sys.path.insert(0, text)


__all__ = [
    "Prereq",
    "checkout_prereq",
    "ensure_on_path",
    "git_run",
    "package_repo_root",
    "third_party_root",
    "verify_pin",
]
