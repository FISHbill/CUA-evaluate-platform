"""Table 1 渲染：单值、双指标、ASR 反向、infra 跳过。"""

from __future__ import annotations

from cua_eval.report.table1 import (
    LOWER_IS_BETTER_MARK,
    Table1Column,
    format_metric_set,
    render_table1,
)
from cua_eval.schema import Metric, MetricSet


def _single(value: float, *, infra: int = 0, scored: int | None = None) -> MetricSet:
    n = scored if scored is not None else 2
    metrics = [
        Metric(name="success_rate", value=value, higher_is_better=True, unit="percent"),
    ]
    return MetricSet(metrics=metrics, scored=n, infra_skipped=infra, total=n + infra)


def test_single_metric_percent() -> None:
    text = render_table1(
        [
            Table1Column(
                model="unit-vlm",
                endpoint_kind="api",
                harness="stub",
                protocol="screenshot+mouse_keyboard",
                cells={"fake": _single(50.0)},
            )
        ]
    )
    assert "50.0" in text
    assert "0.5" not in text  # 聚合必须是百分数，不是 0–1
    assert "unit-vlm / api / stub / screenshot+mouse_keyboard" in text
    assert "| fake |" in text or "| fake | 50.0 |" in text


def test_dual_metrics_slash() -> None:
    metric_set = MetricSet(
        metrics=[
            Metric(name="binary", value=18.5, higher_is_better=True, unit="percent"),
            Metric(name="partial", value=48.4, higher_is_better=True, unit="percent"),
        ],
        scored=2,
        total=2,
    )
    cell = format_metric_set(metric_set)
    assert cell == "18.5 / 48.4"
    text = render_table1(
        [
            Table1Column(
                model="m",
                endpoint_kind="local",
                harness="deepseek_harness",
                protocol="screenshot+mouse_keyboard+guest_shell",
                cells={"osworld_2": metric_set},
            )
        ]
    )
    assert "18.5 / 48.4" in text
    assert "local" in text


def test_asr_annotated_and_sorted_lower_first() -> None:
    low = MetricSet(
        metrics=[Metric(name="asr", value=16.4, higher_is_better=False, unit="percent")],
        scored=4,
        total=4,
    )
    high = MetricSet(
        metrics=[Metric(name="asr", value=40.0, higher_is_better=False, unit="percent")],
        scored=4,
        total=4,
    )
    text = render_table1(
        [
            Table1Column(
                model="noisy",
                endpoint_kind="api",
                harness="stub",
                protocol="screenshot+mouse_keyboard",
                cells={"redteam": high},
            ),
            Table1Column(
                model="quiet",
                endpoint_kind="api",
                harness="stub",
                protocol="screenshot+mouse_keyboard",
                cells={"redteam": low},
            ),
        ],
        sort_by="asr",
    )
    assert f"16.4 {LOWER_IS_BETTER_MARK}" in text
    assert f"40.0 {LOWER_IS_BETTER_MARK}" in text
    assert "越低越好" in text
    quiet_at = text.index("quiet")
    noisy_at = text.index("noisy")
    assert quiet_at < noisy_at


def test_infra_skipped_footnote() -> None:
    metric_set = _single(100.0, infra=1, scored=2)
    text = render_table1(
        [
            Table1Column(
                model="unit-vlm",
                endpoint_kind="api",
                harness="stub",
                protocol="screenshot+mouse_keyboard",
                cells={"fake": metric_set},
            )
        ]
    )
    assert "2/2 scored, 1 infra skipped" in text
    assert "100.0" in text


def test_all_infra_is_emdash() -> None:
    metric_set = MetricSet(scored=0, infra_skipped=1, total=1)
    assert format_metric_set(metric_set) == "—"
    text = render_table1(
        [
            Table1Column(
                model="unit-vlm",
                endpoint_kind="api",
                harness="stub",
                protocol="screenshot+mouse_keyboard",
                cells={"fake": metric_set},
            )
        ]
    )
    assert "—" in text
    assert "0/0 scored, 1 infra skipped" in text


def test_dummy_footnote() -> None:
    text = render_table1(
        [
            Table1Column(
                model="dummy-fixed-actions",
                endpoint_kind="dummy",
                harness="stub",
                protocol="screenshot+mouse_keyboard",
                cells={"fake": _single(100.0, scored=1)},
                dummy=True,
            )
        ]
    )
    assert "dummy 的分数不是模型能力" in text
