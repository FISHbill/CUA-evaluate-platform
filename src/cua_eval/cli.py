"""`cua-eval` 命令行入口。"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from cua_eval import __version__
from cua_eval.errors import ConfigError, UnsupportedBenchError, UnsupportedComputeBackend
from cua_eval.orchestrator.run import run_experiment
from cua_eval.schema import Experiment

app = typer.Typer(
    name="cua-eval",
    help="评测『模型 + harness』组成的 Computer-Use Agent 在公开 benchmark 上的表现。",
    no_args_is_help=True,
    add_completion=False,
)

# 未实现的子命令用这个退出码，与「配置不合法」（1）区分开。
EXIT_NOT_IMPLEMENTED = 2


def _not_implemented(command: str, milestone: str) -> None:
    typer.secho(
        f"`cua-eval {command}` 尚未实现，由 {milestone} 交付（见 docs/EXECUTION_PLAN.md）。",
        fg=typer.colors.YELLOW,
        err=True,
    )
    raise typer.Exit(code=EXIT_NOT_IMPLEMENTED)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"cua-eval {__version__}")
        raise typer.Exit


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option("--version", callback=_version_callback, is_eager=True, help="打印版本后退出"),
    ] = False,
) -> None:
    """CUA 评测平台。"""


@app.command()
def doctor() -> None:
    """检查 Python / Docker / KVM / 磁盘 / 模型端点是否就绪。

    缺项时退出非 0 并说明缺什么，绝不静默降级成 dummy 还宣称「已验证模型」。
    """
    _not_implemented("doctor", "M2")


@app.command()
def run(
    config: Annotated[
        Path,
        typer.Option("-c", "--config", exists=True, dir_okay=False, help="实验配置 YAML"),
    ],
) -> None:
    """跑一次实验。"""
    try:
        experiment = Experiment.from_yaml(config)
        record = run_experiment(experiment)
    except ConfigError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    except UnsupportedComputeBackend as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    except UnsupportedBenchError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    key = record.evaluation_key()
    typer.echo(f"run_id         {record.run_id}")
    typer.echo(f"实验名         {experiment.name}")
    typer.echo(f"bench          {key.bench} @ {key.bench_version}")
    typer.echo(f"模型           {key.model} ({key.endpoint_kind})")
    typer.echo(f"harness        {key.harness}")
    typer.echo(f"协议           {key.protocol}")
    typer.echo(f"计算后端       {key.compute_backend}")
    for trial in record.trials:
        score = "—" if trial.score is None else f"{trial.score:.3f}"
        typer.echo(
            f"  {trial.task_id}: {trial.failure_class.value}  score={score}  steps={trial.steps}"
        )
        if trial.failure_class.value == "ok" and experiment.agent.model.backend.value == "dummy":
            typer.echo("  注：dummy 的分数不是模型能力，只说明平台链路通了。")
    typer.echo(f"结果目录       {experiment.results_dir / record.run_id}")


@app.command()
def report(
    run_id: Annotated[str, typer.Argument(help="results/ 下的 run_id")],
) -> None:
    """把 Table 1 风格文本打到 stdout，并写 results/<run_id>/table1.md。"""
    _not_implemented("report", "M2")


@app.command()
def prune() -> None:
    """按 artifact_retention_days 清理过期的 results/<run_id>。"""
    _not_implemented("prune", "M2")


if __name__ == "__main__":  # pragma: no cover
    app()
