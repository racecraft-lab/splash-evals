from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

import local_evals.publication as publication
from local_evals.publication import prepare_publication


def _policies() -> dict[str, Any]:
    return {
        "publication": {
            "max_file_bytes": 100_000,
            "allowed_suffixes": [".md", ".py", ".json", ".yaml", ".yml", ".toml"],
            "denied_suffixes": [".zip", ".tar", ".gz", ".ipynb", ".sqlite", ".db"],
        }
    }


def _init_identity_repo(tmp_path: Path):
    approved_name = "Racecraft Lab Automation"
    approved_email = "info" + "@racecraft.co"
    git = shutil.which("git")
    assert git is not None

    def run_git(*args: str, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603 - resolved executable and test arguments.
            [git, *args],
            cwd=tmp_path,
            check=True,
            capture_output=True,
            text=True,
            input=input_text,
        )

    run_git("init", "-b", "main")
    run_git("config", "--local", "user.name", approved_name)
    run_git("config", "--local", "user.email", approved_email)
    run_git("config", "--local", "commit.gpgsign", "false")
    (tmp_path / "record.md").write_text("approved branch\n", encoding="utf-8")
    run_git("add", "record.md")
    run_git("commit", "-m", "Approved root")
    return run_git, approved_name, approved_email


def _set_approved_identity(monkeypatch: pytest.MonkeyPatch, name: str, email: str) -> None:
    monkeypatch.setenv("PUBLIC_GIT_AUTHOR_NAME", name)
    monkeypatch.setenv("PUBLIC_GIT_AUTHOR_EMAIL", email)


def test_publication_scan_rejects_symlink_even_when_target_is_safe(tmp_path: Path) -> None:
    target = tmp_path / "safe.md"
    target.write_text("synthetic safe content\n", encoding="utf-8")
    link = tmp_path / "linked.md"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks unavailable")

    findings = publication._scan_file(link, tmp_path, _policies())

    assert findings == [{"rule": "symlink-excluded", "path": "linked.md", "severity": "block"}]


@pytest.mark.parametrize("filename", ["results.zip", "bundle.tar", "captured.ipynb", "runs.sqlite"])
def test_publication_scan_rejects_archives_not_just_their_contents(
    tmp_path: Path, filename: str
) -> None:
    candidate = tmp_path / filename
    candidate.write_bytes(b"synthetic archive sentinel")

    findings = publication._scan_file(candidate, tmp_path, _policies())

    assert any(finding["rule"] == "uninspectable-or-denied-type" for finding in findings)


def test_publication_scan_detects_private_path_without_echoing_it(tmp_path: Path) -> None:
    private_path_sentinel = "/" + "Users/synthetic-operator/private-project/result.json"
    candidate = tmp_path / "report.md"
    candidate.write_text(f"Private location: {private_path_sentinel}\n", encoding="utf-8")

    findings = publication._scan_file(candidate, tmp_path, _policies())

    assert any(finding["rule"] == "absolute-user-path" for finding in findings)
    serialized = repr(findings)
    assert private_path_sentinel not in serialized
    assert all(finding.get("matched_text_redacted") is True for finding in findings)


def test_publication_scan_detects_synthetic_email_without_echoing_it(tmp_path: Path) -> None:
    email_sentinel = "synthetic.operator" + "@example.invalid"
    candidate = tmp_path / "metadata.json"
    candidate.write_text(f'{{"author_email":"{email_sentinel}"}}\n', encoding="utf-8")

    findings = publication._scan_file(candidate, tmp_path, _policies())

    assert any(finding["rule"] == "email-address" for finding in findings)
    assert email_sentinel not in repr(findings)


def test_publication_scan_allows_only_exact_approved_public_email(tmp_path: Path) -> None:
    approved = "support" + "@racecraft.co"
    unapproved = "private" + "@racecraft.co"
    policies = _policies()
    policies["publication"]["allowed_public_emails"] = [approved]
    candidate = tmp_path / "conduct.md"
    candidate.write_text(f"Approved: {approved}\n", encoding="utf-8")

    assert not any(
        finding["rule"] == "email-address"
        for finding in publication._scan_file(candidate, tmp_path, policies)
    )

    candidate.write_text(f"Unapproved: {unapproved}\n", encoding="utf-8")
    findings = publication._scan_file(candidate, tmp_path, policies)

    assert any(finding["rule"] == "email-address" for finding in findings)
    assert unapproved not in repr(findings)


def test_identity_audit_checks_only_commits_reachable_from_head(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    approved_name = "Racecraft Lab Automation"
    approved_email = "info" + "@racecraft.co"
    other_email = "other" + "@example.invalid"
    git = shutil.which("git")
    assert git is not None

    def run_git(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603 - resolved executable and internal test arguments.
            [git, *args], cwd=tmp_path, check=True, capture_output=True, text=True
        )

    run_git("init", "-b", "main")
    run_git("config", "--local", "user.name", approved_name)
    run_git("config", "--local", "user.email", approved_email)
    run_git("config", "--local", "commit.gpgsign", "false")
    (tmp_path / "record.md").write_text("approved branch\n", encoding="utf-8")
    run_git("add", "record.md")
    run_git("commit", "-m", "Approved root")

    run_git("checkout", "-b", "unrelated")
    (tmp_path / "record.md").write_text("unrelated ref\n", encoding="utf-8")
    run_git("add", "record.md")
    run_git(
        "-c",
        "user.name=Unrelated Contributor",
        "-c",
        f"user.email={other_email}",
        "commit",
        "-m",
        "Unrelated ref commit",
    )
    run_git("checkout", "main")
    monkeypatch.setenv("PUBLIC_GIT_AUTHOR_NAME", approved_name)
    monkeypatch.setenv("PUBLIC_GIT_AUTHOR_EMAIL", approved_email)

    findings, summary = publication._identity_findings(tmp_path)

    assert findings == []
    assert not any(finding["rule"] == "history-identity-mismatch" for finding in findings)
    assert summary["commits_checked"] == 1


def test_identity_audit_scans_commit_body_and_trailers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run_git, approved_name, approved_email = _init_identity_repo(tmp_path)
    _set_approved_identity(monkeypatch, approved_name, approved_email)
    private_path = "/" + "Users/synthetic/private"
    (tmp_path / "record.md").write_text("updated\n", encoding="utf-8")
    run_git("add", "record.md")
    run_git(
        "commit",
        "-m",
        f"Approved subject\n\nBody text\n\nPrivate-Path: {private_path}\n",
    )

    findings, summary = publication._identity_findings(tmp_path, _policies())

    assert any(finding["rule"] == "commit-message-absolute-user-path" for finding in findings)
    assert summary["commits_checked"] == 2


def test_signed_commit_without_proven_approved_signer_requires_manual_review(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run_git, approved_name, approved_email = _init_identity_repo(tmp_path)
    _set_approved_identity(monkeypatch, approved_name, approved_email)
    tree = run_git("write-tree").stdout.strip()
    parent = run_git("rev-parse", "HEAD").stdout.strip()
    signed_commit = (
        f"tree {tree}\n"
        f"parent {parent}\n"
        f"author {approved_name} <{approved_email}> 1700000000 +0000\n"
        f"committer {approved_name} <{approved_email}> 1700000000 +0000\n"
        "gpgsig -----BEGIN PGP SIGNATURE-----\n"
        " invalid synthetic signature\n"
        " -----END PGP SIGNATURE-----\n"
        "\nSigned commit body\n"
    )
    commit_hash = run_git("hash-object", "-t", "commit", "-w", "--stdin", input_text=signed_commit)
    run_git("update-ref", "refs/heads/main", commit_hash.stdout.strip())

    findings, summary = publication._identity_findings(tmp_path, _policies())

    assert any(finding["rule"] == "signed-commit-requires-manual-review" for finding in findings)
    assert summary["signed_commits_checked"] == 1


def test_annotated_tag_identity_and_full_content_are_scanned(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run_git, approved_name, approved_email = _init_identity_repo(tmp_path)
    _set_approved_identity(monkeypatch, approved_name, approved_email)
    private_path = "/" + "Users/synthetic/tag"
    unapproved_email = "unapproved" + "@example.invalid"
    run_git(
        "-c",
        "user.name=Unapproved Tagger",
        "-c",
        f"user.email={unapproved_email}",
        "tag",
        "-a",
        "v-bad",
        "-m",
        f"Tag body\n\nPrivate-Path: {private_path}\n",
        "HEAD",
    )

    findings, summary = publication._identity_findings(tmp_path, _policies())
    rules = {finding["rule"] for finding in findings}

    assert "tag-identity-mismatch" in rules
    assert "tag-content-absolute-user-path" in rules
    assert summary["annotated_tags_checked"] == 1
    assert summary["tag_target_types"] == {"commit": 1}


def test_signed_annotated_tag_requires_manual_review(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run_git, approved_name, approved_email = _init_identity_repo(tmp_path)
    _set_approved_identity(monkeypatch, approved_name, approved_email)
    target = run_git("rev-parse", "HEAD").stdout.strip()
    tag_object = (
        f"object {target}\n"
        "type commit\n"
        "tag v-signed\n"
        f"tagger {approved_name} <{approved_email}> 1700000000 +0000\n"
        "\nSigned tag body\n"
        "-----BEGIN PGP SIGNATURE-----\n"
        "invalid synthetic signature\n"
        "-----END PGP SIGNATURE-----\n"
    )
    tag_hash = run_git("hash-object", "-t", "tag", "-w", "--stdin", input_text=tag_object)
    run_git("update-ref", "refs/tags/v-signed", tag_hash.stdout.strip())

    findings, summary = publication._identity_findings(tmp_path, _policies())

    assert any(finding["rule"] == "signed-tag-requires-manual-review" for finding in findings)
    assert summary["signed_tags_checked"] == 1


@pytest.mark.parametrize(
    ("begin_marker", "end_marker"),
    [
        ("-----BEGIN PGP MESSAGE-----", "-----END PGP MESSAGE-----"),
        ("-----BEGIN SIGNED MESSAGE-----", "-----END SIGNED MESSAGE-----"),
    ],
)
def test_additional_annotated_tag_signature_armors_require_manual_review(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    begin_marker: str,
    end_marker: str,
) -> None:
    run_git, approved_name, approved_email = _init_identity_repo(tmp_path)
    _set_approved_identity(monkeypatch, approved_name, approved_email)
    target = run_git("rev-parse", "HEAD").stdout.strip()
    tag_object = (
        f"object {target}\n"
        "type commit\n"
        "tag v-signed-variant\n"
        f"tagger {approved_name} <{approved_email}> 1700000000 +0000\n"
        f"\nSigned tag body\n{begin_marker}\n"
        "invalid synthetic signature\n"
        f"{end_marker}\n"
    )
    tag_hash = run_git("hash-object", "-t", "tag", "-w", "--stdin", input_text=tag_object)
    run_git("update-ref", "refs/tags/v-signed-variant", tag_hash.stdout.strip())

    findings, summary = publication._identity_findings(tmp_path, _policies())

    assert any(finding["rule"] == "signed-tag-requires-manual-review" for finding in findings)
    assert summary["signed_tags_checked"] == 1


def test_mergetag_header_requires_manual_review(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run_git, approved_name, approved_email = _init_identity_repo(tmp_path)
    _set_approved_identity(monkeypatch, approved_name, approved_email)
    tree = run_git("write-tree").stdout.strip()
    parent = run_git("rev-parse", "HEAD").stdout.strip()
    mergetag_commit = (
        f"tree {tree}\n"
        f"parent {parent}\n"
        f"author {approved_name} <{approved_email}> 1700000000 +0000\n"
        f"committer {approved_name} <{approved_email}> 1700000000 +0000\n"
        "mergetag object " + parent + "\n"
        " type commit\n"
        " tag v-merged\n"
        f" tagger {approved_name} <{approved_email}> 1700000000 +0000\n"
        " -----BEGIN PGP SIGNATURE-----\n"
        " synthetic mergetag signature\n"
        " -----END PGP SIGNATURE-----\n"
        " \n"
        "\n"
        "Merge commit body\n"
    )
    commit_hash = run_git(
        "hash-object", "-t", "commit", "-w", "--stdin", input_text=mergetag_commit
    )
    run_git("update-ref", "refs/heads/main", commit_hash.stdout.strip())

    findings, summary = publication._identity_findings(tmp_path, _policies())

    assert any(finding["rule"] == "signed-mergetag-requires-manual-review" for finding in findings)
    assert summary["commits_checked"] == 2


def test_annotated_tag_header_must_match_reached_ref(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run_git, approved_name, approved_email = _init_identity_repo(tmp_path)
    _set_approved_identity(monkeypatch, approved_name, approved_email)
    target = run_git("rev-parse", "HEAD").stdout.strip()
    tag_object = (
        f"object {target}\n"
        "type commit\n"
        "tag v-other\n" + f"tagger {approved_name} <{approved_email}> 1700000000 +0000\n"
        "\nSynthetic tag body\n"
    )
    tag_hash = run_git("hash-object", "-t", "tag", "-w", "--stdin", input_text=tag_object)
    run_git("update-ref", "refs/tags/v-ref", tag_hash.stdout.strip())

    findings, summary = publication._identity_findings(tmp_path, _policies())

    assert any(finding["rule"] == "tag-metadata-name-mismatch" for finding in findings)
    assert summary["annotated_tags_checked"] == 1


def test_lightweight_tag_requires_manual_review(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run_git, approved_name, approved_email = _init_identity_repo(tmp_path)
    _set_approved_identity(monkeypatch, approved_name, approved_email)
    run_git("tag", "v-lightweight", "HEAD")

    findings, summary = publication._identity_findings(tmp_path, _policies())

    assert any(finding["rule"] == "lightweight-tag-requires-manual-review" for finding in findings)
    assert summary["lightweight_tags_checked"] == 1


def test_publish_prepare_exports_allowlisted_aggregate_fields_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manifest = {
        "evidence_class": "local_measurement",
        "model_is_splash": True,
        "suite": "pilot",
        "held_out": True,
        "protocol": {"task_set": "synthetic-held-out-v1", "scorer_version": "synthetic-v1"},
        "selection_hash": "synthetic-selection-hash",
        "aggregate": {"successes": 2, "completed": 3},
        "primary_objective_status_if_run": "pilot_only",
        "limitations": ["Synthetic unit-test record; not a model result."],
        "private_task_manifest": [{"prompt": "must not export"}],
        "raw_response": {"content": "must not export"},
        "instance_id": "must-not-export",
        "started_at": "2026-09-19T00:00:00Z",
        "local_path": "/" + "Users/synthetic-operator/private",
    }
    monkeypatch.setattr(publication, "load_run", lambda run_id, root=None: (manifest, []))
    monkeypatch.setattr(
        publication,
        "audit_publication",
        lambda **kwargs: {"status": "pass", "findings": []},
    )

    result = prepare_publication("synthetic-run", dry_run=True, root=tmp_path)

    assert result["status"] == "dry_run_ready_for_human_review"
    assert result["writes_performed"] is False
    preview = result["preview"]
    assert set(preview) == set(result["allowlisted_fields"])
    serialized = repr(preview)
    for forbidden in (
        "must not export",
        "must-not-export",
        "2026-09-19T00:00:00Z",
        manifest["local_path"],
        "private_task_manifest",
        "raw_response",
        "instance_id",
    ):
        assert forbidden not in serialized


def test_public_export_omits_raw_run_id_from_payload_and_markdown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    raw_run_id = "splash-pilot-20260919T204522Z"
    manifest = {
        "evidence_class": "local_measurement",
        "model_is_splash": True,
        "suite": "pilot",
        "held_out": True,
        "protocol": {"task_set": "synthetic-held-out-v1", "scorer_version": "synthetic-v1"},
        "aggregate": {"successes": 2, "completed": 3},
        "primary_objective_status_if_run": "pilot_only",
        "limitations": [raw_run_id],
    }
    monkeypatch.setattr(publication, "load_run", lambda run_id, root=None: (manifest, []))
    monkeypatch.setattr(
        publication,
        "audit_publication",
        lambda **kwargs: {"status": "pass", "findings": []},
    )
    state_dir = tmp_path / "external-state"
    monkeypatch.setattr(publication, "get_state_dir", lambda root=None: state_dir)

    result = prepare_publication(raw_run_id, dry_run=False, root=tmp_path)
    export_dir = Path(result["export_directory"])
    payload = json.loads((export_dir / "aggregate.json").read_text(encoding="utf-8"))
    markdown = (export_dir / "README.md").read_text(encoding="utf-8")

    assert payload["run_label"] == "local-pilot"
    assert "run_id" not in payload
    assert raw_run_id not in repr(payload)
    assert raw_run_id not in markdown


def test_mock_harness_run_is_blocked_from_capability_export(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manifest = {
        "evidence_class": "synthetic_harness",
        "model_is_splash": False,
        "suite": "smoke",
        "protocol": {"task_set": "synthetic-smoke", "scorer_version": "synthetic-v1"},
        "aggregate": {"successes": 12, "completed": 12},
        "primary_objective_status_if_run": "blocked",
    }
    monkeypatch.setattr(publication, "load_run", lambda run_id, root=None: (manifest, []))
    monkeypatch.setattr(
        publication,
        "audit_publication",
        lambda **kwargs: {"status": "pass", "findings": []},
    )

    result = prepare_publication("synthetic-smoke-run", dry_run=True, root=tmp_path)

    assert result["status"] == "dry_run_blocked"
    assert any(
        blocker["rule"] == "non-held-out-result-not-capability-evidence"
        for blocker in result["blockers"]
    )


def test_calibration_run_is_blocked_from_capability_export(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manifest = {
        "evidence_class": "local_measurement",
        "model_is_splash": True,
        "suite": "calibration",
        "held_out": False,
        "protocol": {"task_set": "synthetic-calibration", "scorer_version": "synthetic-v1"},
        "aggregate": {"successes": 20, "completed": 24},
        "primary_objective_status_if_run": "blocked",
    }
    monkeypatch.setattr(publication, "load_run", lambda run_id, root=None: (manifest, []))
    monkeypatch.setattr(
        publication,
        "audit_publication",
        lambda **kwargs: {"status": "pass", "findings": []},
    )

    result = prepare_publication("synthetic-calibration-run", dry_run=True, root=tmp_path)

    assert result["status"] == "dry_run_blocked"
    assert any(
        blocker["rule"] == "non-held-out-result-not-capability-evidence"
        for blocker in result["blockers"]
    )
