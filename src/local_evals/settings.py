"""Immutable setting snapshots, append-only journals, exports, and safe restore plans."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any

from .config import ConfigurationError, secure_resolve
from .models import (
    ChangeJournalEntry,
    RestoreAction,
    RestorePlan,
    RestoreStatus,
    SettingEvidenceRecord,
    StateSnapshot,
)


class SettingsError(RuntimeError):
    pass


class InterveningChangeError(SettingsError):
    pass


_SECRET_KEY = re.compile(
    r"(^|_)(password|secret|access_token|api_token|api_key|auth_token|bearer_token|token)$",
    re.I,
)


def _json_value(value: Any) -> Any:
    """Copy settings through strict JSON to prevent later object mutation."""

    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise SettingsError("settings must contain finite JSON-compatible values") from exc


def _reject_secret_values(value: Any, path: str = "settings") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            label = str(key)
            if _SECRET_KEY.search(label) and child not in (None, "", False):
                raise SettingsError(f"snapshot must store {path}.{label} only by reference")
            _reject_secret_values(child, f"{path}.{label}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_secret_values(child, f"{path}[{index}]")


def settings_digest(settings: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        _json_value(dict(settings)), sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def create_snapshot(
    settings: Mapping[str, Any],
    *,
    source_fingerprint: str,
    secret_references: Sequence[str] = (),
) -> StateSnapshot:
    copied = _json_value(dict(settings))
    _reject_secret_values(copied)
    digest = settings_digest(copied)
    identity = hashlib.sha256(f"{source_fingerprint}:{digest}".encode()).hexdigest()[:20]
    return StateSnapshot(
        snapshot_id=f"snapshot-{identity}",
        settings=copied,
        settings_digest=digest,
        source_fingerprint=source_fingerprint,
        secret_references=tuple(sorted(set(secret_references))),
    )


def _private_directory(path: Path) -> Path:
    resolved = secure_resolve(path, must_exist=True)
    if not resolved.is_dir():
        raise ConfigurationError("snapshot destination is not a directory")
    if resolved.stat().st_mode & 0o077:
        raise ConfigurationError("snapshot destination permissions must be 0700 or stricter")
    return resolved


def persist_snapshot(snapshot: StateSnapshot, directory: Path) -> Path:
    """Persist once with mode 0600; an existing snapshot is never overwritten."""

    destination = _private_directory(directory) / f"{snapshot.snapshot_id}.json"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        descriptor = os.open(destination, flags, 0o600)
    except FileExistsError as exc:
        raise SettingsError("snapshot already exists and is immutable") from exc
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(snapshot.model_dump(mode="json"), handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    return destination


def snapshot(
    settings: Mapping[str, Any],
    *,
    source_fingerprint: str,
    directory: Path | None = None,
    secret_references: Sequence[str] = (),
) -> StateSnapshot:
    record = create_snapshot(
        settings,
        source_fingerprint=source_fingerprint,
        secret_references=secret_references,
    )
    if directory is not None:
        persist_snapshot(record, directory)
    return record


def record_change(
    journal: Sequence[ChangeJournalEntry],
    *,
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    reason: str,
) -> ChangeJournalEntry:
    if not reason.strip():
        raise SettingsError("change journal reason must not be empty")
    before_copy = _json_value(dict(before))
    after_copy = _json_value(dict(after))
    if journal and journal[-1].after_digest != settings_digest(before_copy):
        raise InterveningChangeError("new change does not continue the existing journal")
    return ChangeJournalEntry(
        sequence=(journal[-1].sequence + 1) if journal else 1,
        reason=reason,
        before_digest=settings_digest(before_copy),
        after_digest=settings_digest(after_copy),
        before=before_copy,
        after=after_copy,
    )


def append_journal(entry: ChangeJournalEntry, path: Path) -> None:
    """Append a JSON line under a restrictive external-state directory."""

    parent = _private_directory(path.parent)
    destination = secure_resolve(parent / path.name)
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
    descriptor = os.open(destination, flags, 0o600)
    with os.fdopen(descriptor, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry.model_dump(mode="json"), sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(value, Mapping):
        flattened: dict[str, Any] = {}
        for key in sorted(value):
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            flattened.update(_flatten(value[key], child_prefix))
        return flattened
    return {prefix: value}


def plan_restore(
    snapshot_record: StateSnapshot,
    *,
    current_settings: Mapping[str, Any],
    journal: Sequence[ChangeJournalEntry],
) -> RestorePlan:
    current_copy = _json_value(dict(current_settings))
    current_digest = settings_digest(current_copy)
    expected = journal[-1].after_digest if journal else snapshot_record.settings_digest
    if current_digest != expected:
        return RestorePlan(
            snapshot_id=snapshot_record.snapshot_id,
            status=RestoreStatus.BLOCKED_INTERVENING_CHANGE,
            current_digest=current_digest,
            expected_current_digest=expected,
            target_digest=snapshot_record.settings_digest,
            reasons=("current settings differ from the last project-written state",),
        )
    if current_digest == snapshot_record.settings_digest:
        return RestorePlan(
            snapshot_id=snapshot_record.snapshot_id,
            status=RestoreStatus.NO_CHANGES,
            current_digest=current_digest,
            expected_current_digest=expected,
            target_digest=snapshot_record.settings_digest,
            reasons=("settings already match the snapshot",),
        )
    current_flat = _flatten(current_copy)
    target_flat = _flatten(snapshot_record.settings)
    names = sorted(set(current_flat) | set(target_flat))
    actions = tuple(
        RestoreAction(
            setting=name,
            current=deepcopy(current_flat.get(name)),
            restore_to=deepcopy(target_flat.get(name)),
        )
        for name in names
        if current_flat.get(name) != target_flat.get(name)
    )
    return RestorePlan(
        snapshot_id=snapshot_record.snapshot_id,
        status=RestoreStatus.READY,
        current_digest=current_digest,
        expected_current_digest=expected,
        target_digest=snapshot_record.settings_digest,
        actions=actions,
    )


def restore(
    snapshot_record: StateSnapshot,
    *,
    read_current: Callable[[], Mapping[str, Any]],
    journal: Sequence[ChangeJournalEntry],
    dry_run: bool = True,
    apply: Callable[[Mapping[str, Any]], None] | None = None,
) -> RestorePlan:
    """Plan by default; recheck immediately before any explicitly requested apply."""

    plan = plan_restore(snapshot_record, current_settings=read_current(), journal=journal)
    if dry_run or plan.status is not RestoreStatus.READY:
        return plan
    if apply is None:
        raise SettingsError("non-dry-run restore requires an explicit settings writer")
    rechecked = plan_restore(snapshot_record, current_settings=read_current(), journal=journal)
    changed_since_plan = rechecked.current_digest != plan.current_digest
    if rechecked.status is not RestoreStatus.READY or changed_since_plan:
        raise InterveningChangeError("settings changed after restore planning")
    apply(deepcopy(snapshot_record.settings))
    observed = settings_digest(read_current())
    if observed != snapshot_record.settings_digest:
        raise SettingsError("restored settings did not match the snapshot on read-back")
    return rechecked.model_copy(update={"dry_run": False})


def build_export(
    *,
    profile_id: str,
    model_identity: Mapping[str, Any],
    load_settings: Sequence[SettingEvidenceRecord],
    generation_settings: Sequence[SettingEvidenceRecord],
) -> dict[str, Any]:
    """Build separate records without claiming native importability."""

    return {
        "schema_version": 1,
        "profile_id": profile_id,
        "native_lm_studio_preset": None,
        "native_preset_status": "not_validated",
        "model_identity": _json_value(dict(model_identity)),
        "load_settings": [item.model_dump(mode="json") for item in load_settings],
        "generation_settings": [item.model_dump(mode="json") for item in generation_settings],
    }


def export(document: Mapping[str, Any], destination: Path) -> Path:
    """Atomically replace a project-owned export file, never a personal preset."""

    parent = secure_resolve(destination.parent, must_exist=True)
    target = secure_resolve(parent / destination.name)
    payload = json.dumps(_json_value(dict(document)), sort_keys=True, indent=2) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return target
