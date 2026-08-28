"""`cua-eval` 命令行入口。"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from cua_eval import __version__
from cua_eval.doctor import run_doctor
from cua_eval.errors import ConfigError, UnsupportedBenchError, UnsupportedComputeBackend
from cua_eval.orchestrator.run import run_experiment
from cua_eval.report.table1 import render_run
from cua_eval.schema import Experiment
from cua_eval.store.results import ResultStore, prune_results

app = typer.Typer(
    name="cua-eval",
    help="评测『模型 + harness』组成的 Computer-Use Agent 在公开 benchmark 上的表现。",
    no_args_is_help=True,
    add_completion=False,
)


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
def doctor(
    config: Annotated[
        Path | None,
        typer.Option("-c", "--config", exists=True, dir_okay=False, help="实验配置 YAML（可选）"),
    ] = None,
) -> None:
    """检查 Python / Docker / KVM / 磁盘 / 模型端点是否就绪。

    缺项时退出非 0 并说明缺什么，绝不静默降级成 dummy 还宣称「已验证模型」。
    """
    experiment = None
    if config is not None:
        try:
            experiment = Experiment.from_yaml(config)
        except ConfigError as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from exc
    report = run_doctor(experiment)
    typer.echo(report.render())
    if not report.ok:
        raise typer.Exit(code=1)


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
    results_dir: Annotated[
        Path,
        typer.Option("--results-dir", help="results 根目录"),
    ] = Path("results"),
) -> None:
    """把 Table 1 风格文本打到 stdout，并写 results/<run_id>/table1.md。"""
    store = ResultStore(results_dir)
    try:
        record = store.load_run(run_id)
    except ConfigError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    text = render_run(record)
    typer.echo(text)
    out = results_dir / run_id / "table1.md"
    out.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
    typer.echo(f"wrote {out}", err=True)


@app.command()
def prune(
    config: Annotated[
        Path | None,
        typer.Option("-c", "--config", exists=True, dir_okay=False, help="读 YAML 的 retention"),
    ] = None,
    results_dir: Annotated[
        Path | None,
        typer.Option("--results-dir", help="覆盖 results 根目录"),
    ] = None,
    days: Annotated[
        int | None,
        typer.Option("--days", help="覆盖 artifact_retention_days"),
    ] = None,
) -> None:
    """按 artifact_retention_days 清理过期的 results/<run_id>。"""
    retention = 14
    root = Path("results")
    if config is not None:
        try:
            experiment = Experiment.from_yaml(config)
        except ConfigError as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from exc
        retention = experiment.artifact_retention_days
        root = experiment.results_dir
    if results_dir is not None:
        root = results_dir
    if days is not None:
        if days <= 0:
            typer.secho("--days 必须为正", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1)
        retention = days
    deleted = prune_results(root, retention)
    if not deleted:
        typer.echo(f"nothing to prune under {root} (retention={retention}d)")
        return
    typer.echo(f"deleted {len(deleted)} run(s) older than {retention}d under {root}")
    for path in deleted:
        typer.echo(f"  {path.name}")


if __name__ == "__main__":  # pragma: no cover
    app()
