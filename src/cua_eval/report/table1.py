"""Qwen-CUA Table 1 风格的 Markdown 渲染。

阶段 1 只输出单指标 `success_rate`（百分数）。渲染器仍支持：
- 双指标列（`a / b`，给 OSWorld 2.0 的 binary/partial、RedTeamCUA 的 success/ASR）
- ASR 这类 `higher_is_better=false` 的指标：单元格标注「越低越好」，排序时反向
- `infra_error` 不进分母，脚注写清 skipped 数量
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from cua_eval.schema import EvaluationKey, Metric, MetricSet, ModelBackend, RunRecord

LOWER_IS_BETTER_MARK = "↓"


@dataclass(frozen=True)
class Table1Column:
    """一列 = 一个评测对象（模型 × 端点类型 × harness × 协议）。"""

    model: str
    endpoint_kind: str
    harness: str
    protocol: str
    cells: dict[str, MetricSet]
    dummy: bool = False

    @property
    def header(self) -> str:
        return f"{self.model} / {self.endpoint_kind} / {self.harness} / {self.protocol}"


def format_metric_value(metric: Metric) -> str:
    text = f"{metric.value:.1f}"
    if metric.unit == "percent":
        # 百分数已经在聚合时乘过 100，这里只控制小数位，不写 % 以免和论文表打架。
        pass
    if not metric.higher_is_better:
        text = f"{text} {LOWER_IS_BETTER_MARK}"
    return text


def format_metric_set(metric_set: MetricSet) -> str:
    if not metric_set.metrics:
        return "—"
    return " / ".join(format_metric_value(m) for m in metric_set.metrics)


def infra_footnote(bench: str, metric_set: MetricSet) -> str | None:
    if metric_set.infra_skipped <= 0:
        return None
    return (
        f"{bench}: {metric_set.scored}/{metric_set.scored} scored, "
        f"{metric_set.infra_skipped} infra skipped"
    )


def _sort_key(column: Table1Column, metric_name: str) -> tuple[int, float]:
    for metric_set in column.cells.values():
        metric = metric_set.get(metric_name)
        if metric is None:
            continue
        if metric.higher_is_better:
            return (0, -metric.value)
        return (0, metric.value)
    return (1, 0.0)


def render_table1(
    columns: Sequence[Table1Column],
    *,
    bench_order: Sequence[str] | None = None,
    sort_by: str | None = None,
) -> str:
    if not columns:
        return "# Table 1\n\n（没有可渲染的 run）\n"

    ordered = list(columns)
    if sort_by:
        ordered.sort(key=lambda col: _sort_key(col, sort_by))

    benches: list[str] = []
    if bench_order:
        benches.extend(bench_order)
    for column in ordered:
        for bench in column.cells:
            if bench not in benches:
                benches.append(bench)

    headers = ["Bench", *[col.header for col in ordered]]
    lines = [
        "# Table 1",
        "",
        "列标识为 `model / endpoint_kind / harness / protocol`。"
        "协议不同的 run 不得进同一列。",
        "",
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    footnotes: list[str] = []
    for bench in benches:
        cells = [format_metric_set(col.cells.get(bench, MetricSet())) for col in ordered]
        lines.append("| " + " | ".join([bench, *cells]) + " |")
        for column in ordered:
            metric_set = column.cells.get(bench)
            if metric_set is None:
                continue
            note = infra_footnote(f"{bench} / {column.model}", metric_set)
            if note:
                footnotes.append(note)

    extra: list[str] = []
    if any(
        metric is not None and not metric.higher_is_better
        for column in ordered
        for metric_set in column.cells.values()
        for metric in metric_set.metrics
    ):
        extra.append(f"{LOWER_IS_BETTER_MARK} 表示越低越好（如 ASR），排序时反向。")
    extra.extend(footnotes)
    if any(col.dummy for col in ordered):
        extra.append("dummy 的分数不是模型能力，只说明平台链路通了。")
    if extra:
        lines.append("")
        lines.append("注：")
        lines.extend(f"- {item}" for item in extra)
    lines.append("")
    return "\n".join(lines)


def column_from_run(record: RunRecord) -> Table1Column:
    key: EvaluationKey = record.evaluation_key()
    dummy = record.experiment.agent.model.backend is ModelBackend.DUMMY
    return Table1Column(
        model=key.model,
        endpoint_kind=key.endpoint_kind,
        harness=key.harness,
        protocol=key.protocol,
        cells={key.bench: record.metric_set()},
        dummy=dummy,
    )


def render_run(record: RunRecord) -> str:
    return render_table1([column_from_run(record)])
