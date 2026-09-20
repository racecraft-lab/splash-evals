from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

import pytest

from local_evals.models import RestoreStatus
from local_evals.settings import (
    InterveningChangeError,
    create_snapshot,
    plan_restore,
    record_change,
    restore,
)


def test_restore_plan_blocks_when_user_changed_project_written_state() -> None:
    baseline = {"context_length": 16_384, "temperature": 1.0}
    project_state = {"context_length": 32_768, "temperature": 1.0}
    user_state = {"context_length": 32_768, "temperature": 0.7}
    snapshot = create_snapshot(baseline, source_fingerprint="synthetic-source-v1")
    journal = [
        record_change([], before=baseline, after=project_state, reason="synthetic calibration")
    ]

    plan = plan_restore(snapshot, current_settings=user_state, journal=journal)

    assert plan.status is RestoreStatus.BLOCKED_INTERVENING_CHANGE
    assert plan.actions == ()
    assert any("differ" in reason for reason in plan.reasons)


def test_restore_dry_run_never_calls_writer() -> None:
    baseline = {"context_length": 16_384}
    project_state = {"context_length": 32_768}
    snapshot = create_snapshot(baseline, source_fingerprint="synthetic-source-v1")
    journal = [record_change([], before=baseline, after=project_state, reason="test change")]
    writes: list[Mapping[str, Any]] = []

    plan = restore(
        snapshot,
        read_current=lambda: project_state,
        journal=journal,
        dry_run=True,
        apply=writes.append,
    )

    assert plan.status is RestoreStatus.READY
    assert plan.dry_run is True
    assert writes == []


def test_restore_rechecks_for_intervening_change_before_apply() -> None:
    baseline = {"context_length": 16_384}
    project_state = {"context_length": 32_768}
    intervening_state = {"context_length": 65_536}
    snapshot = create_snapshot(baseline, source_fingerprint="synthetic-source-v1")
    journal = [record_change([], before=baseline, after=project_state, reason="test change")]
    reads = iter((project_state, intervening_state))
    writes: list[Mapping[str, Any]] = []

    with pytest.raises(InterveningChangeError, match="changed after restore planning"):
        restore(
            snapshot,
            read_current=lambda: next(reads),
            journal=journal,
            dry_run=False,
            apply=writes.append,
        )

    assert writes == []


def test_restore_applies_snapshot_and_verifies_readback() -> None:
    baseline = {"context_length": 16_384, "nested": {"thinking": True}}
    current: dict[str, Any] = {"context_length": 32_768, "nested": {"thinking": True}}
    snapshot = create_snapshot(baseline, source_fingerprint="synthetic-source-v1")
    journal = [record_change([], before=baseline, after=current, reason="test change")]

    def read_current() -> Mapping[str, Any]:
        return deepcopy(current)

    def apply(settings: Mapping[str, Any]) -> None:
        current.clear()
        current.update(deepcopy(settings))

    plan = restore(
        snapshot,
        read_current=read_current,
        journal=journal,
        dry_run=False,
        apply=apply,
    )

    assert plan.status is RestoreStatus.READY
    assert plan.dry_run is False
    assert current == baseline
