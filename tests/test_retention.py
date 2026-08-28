"""按 retention 清理 results/<run_id>，不越界、不误删未过期目录。"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cua_eval.store.results import prune_results

NOW = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)


def _run_dir(root: Path, run_id: str, *, age_days: float) -> Path:
    path = root / run_id
    path.mkdir(parents=True)
    (path / "run.json").write_text("{}", encoding="utf-8")
    stamp = (NOW - timedelta(days=age_days)).timestamp()
    os.utime(path, (stamp, stamp))
    return path


def test_deletes_expired_and_keeps_fresh(tmp_path: Path) -> None:
    root = tmp_path / "results"
    old = _run_dir(root, "20260801-000000-aaaa", age_days=15)
    boundary = _run_dir(root, "20260808-000000-bbbb", age_days=14)
    fresh = _run_dir(root, "20260821-000000-cccc", age_days=1)
    deleted = {p.name for p in prune_results(root, 14, now=NOW)}
    assert old.name in deleted
    assert boundary.name in deleted
    assert fresh.name not in deleted
    assert not old.exists()
    assert not boundary.exists()
    assert fresh.exists()


def test_does_not_delete_outside_results(tmp_path: Path) -> None:
    root = tmp_path / "results"
    root.mkdir()
    outsider = tmp_path / "important"
    outsider.mkdir()
    (outsider / "keep.txt").write_text("do not delete", encoding="utf-8")
    escaped = root / "20260801-000000-ffff"
    escaped.symlink_to(outsider)
    deleted = prune_results(root, 1, now=NOW)
    assert deleted == []
    assert outsider.exists()
    assert (outsider / "keep.txt").exists()


def test_ignores_non_run_id_directories(tmp_path: Path) -> None:
    root = tmp_path / "results"
    notes = root / "notes"
    notes.mkdir(parents=True)
    (notes / "readme.txt").write_text("stay", encoding="utf-8")
    stamp = (NOW - timedelta(days=30)).timestamp()
    os.utime(notes, (stamp, stamp))
    deleted = prune_results(root, 14, now=NOW)
    assert deleted == []
    assert notes.exists()


def test_missing_results_dir_is_noop(tmp_path: Path) -> None:
    assert prune_results(tmp_path / "nope", 14, now=NOW) == []
