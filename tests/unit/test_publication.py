from __future__ import annotations

import json
import shutil
import subprocess
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

import local_evals.publication as publication
from local_evals.publication import prepare_publication
from local_evals.sandbox import SandboxPolicy


def _policies() -> dict[str, Any]:
    return {
        "publication": {
            "max_file_bytes": 100_000,
            "allowed_suffixes": [".md", ".py", ".json", ".yaml", ".yml", ".toml"],
            "denied_suffixes": [".zip", ".tar", ".gz", ".ipynb", ".sqlite", ".db"],
        }
    }


def _capability_manifest() -> dict[str, Any]:
    sample_manifest = "a" * 64
    instance_hash = "b" * 64
    scorer_hash = "c" * 64
    calibration_hash = "d" * 64
    image_digest = "sha256:" + ("e" * 64)
    sandbox_policy = {
        "image_reference": f"racecraft/synthetic-grader@{image_digest}",
        "image_digest": image_digest,
    }
    policy = SandboxPolicy(**sandbox_policy)
    attestation_revision = "synthetic-sandbox-v1"
    attestation_file_sha256 = "8" * 64
    fingerprint = "9" * 64
    return {
        "status": "completed",
        "evidence_class": "local_measurement",
        "model_is_splash": True,
        "suite": "pilot",
        "held_out": True,
        "selection_status": "held_out_verified",
        "calibration_heldout_separation": "held_out",
        "task_families": ["reasoning", "coding"],
        "protocol": {
            "task_set": "synthetic-held-out-v1",
            "scorer_version": "qualified-exact-v1",
        },
        "historical_protocol": {
            "benchmark_version": "synthetic-benchmark-v1",
            "dataset_revision": "synthetic-dataset-revision-v1",
            "split": "held_out",
            "sample_id_manifest": sample_manifest,
            "sample_count": 3,
            "prompts_or_template_revision": "synthetic-template-v1",
            "attempts_per_task": 1,
            "failure_policy": "count_failures_as_incorrect",
            "denominator": "all_planned_samples",
            "scorer_revision": "qualified-exact-v1",
        },
        "selection_evidence": {
            "ordered_sample_manifest_sha256": sample_manifest,
            "manifest_source": "external",
            "frozen_before_tuning": True,
            "contamination_review_revision": "synthetic-review-v1",
        },
        "locality_evidence": {
            "status": "verified_local",
            "endpoint_loopback": True,
            "local_instance_evidence": True,
        },
        "model_instance_evidence": {
            "selection": "exact_loaded_record",
            "splash_attribution": "confirmed",
            "instance_id_sha256": instance_hash,
            "native_identity": {"loaded_instance_id_match": True},
        },
        "served_model_evidence": {
            "status": "verified",
            "match": True,
            "requested_instance_id_sha256": instance_hash,
            "response_instance_id_sha256": instance_hash,
        },
        "runtime_evidence": {
            "transport": "lmstudio_native_v1",
            "endpoint": "/api/v1/chat",
            "cli_version": "synthetic-cli-v1",
            "engine": "synthetic-engine",
            "engine_version": "synthetic-engine-v1",
        },
        "transport": {"redirects": False, "cloud_fallback": False},
        "reasoning_evidence": {
            "requested": "high",
            "transmitted": "high",
            "effective_status": "accepted_by_runtime",
        },
        "scorer_evidence": {
            "scorer_id": "qualified-exact-v1",
            "content_sha256": scorer_hash,
            "namespace": "benchmark",
            "evidence_class": "scorer_qualification",
            "eligible_for_capability_report": True,
            "calibration": {
                "status": "qualified",
                "manifest_sha256": calibration_hash,
                "revision": "synthetic-calibration-v1",
                "independent_from_evaluation": True,
            },
        },
        "sandbox_policy": sandbox_policy,
        "sandbox_attestation": {
            "schema_version": 1,
            "attestation_revision": attestation_revision,
            "qualified": True,
            "container_runtime": "docker",
            "platform": policy.platform,
            "image_reference": policy.image_reference,
            "image_digest": policy.image_digest,
            "policy_sha256": policy.sha256,
            "disposable": True,
            "fresh_container_per_case": True,
            "network_mode": "none",
            "network_disabled": True,
            "non_root_user": policy.user,
            "read_only_rootfs": True,
            "cap_drop": ["ALL"],
            "no_new_privileges": True,
            "pids_limit": policy.pids_limit,
            "memory_bytes": policy.memory_bytes,
            "cpus": policy.cpus,
            "nofile_limit": policy.nofile_limit,
            "tmpfs_bytes": policy.tmpfs_bytes,
            "published_ports": [],
            "host_mounts": [],
            "host_home_mounted": False,
            "docker_socket_mounted": False,
            "secrets_present": False,
        },
        "sandbox_qualification": {
            "schema_version": 1,
            "qualified": True,
            "attestation_revision": attestation_revision,
            "image_digest": policy.image_digest,
            "policy_sha256": policy.sha256,
            "qualification_event_count": 17,
        },
        "sandbox_attestation_sha256": attestation_file_sha256,
        "coding_run_binding": {
            "attestation_file_sha256": attestation_file_sha256,
            "attestation_revision": attestation_revision,
            "denominator_count": 1,
            "denominator_policy": "all_attempted_cases",
            "frozen_before_execution": True,
            "image_digest": policy.image_digest,
            "planned_case_count": 1,
            "attempted_case_count": 1,
            "policy_sha256": policy.sha256,
            "run_fingerprint_sha256": fingerprint,
        },
        "fingerprint": fingerprint,
        "selection_hash": sample_manifest,
        "aggregate": {
            "planned": 3,
            "attempted": 3,
            "completed": 3,
            "scorable": 3,
            "failed": 0,
            "censored": 0,
            "unattempted": 0,
        },
        "primary_objective_status_if_run": "pilot_only",
        "limitations": ["Synthetic unit-test record; not a model result."],
    }


def _core_capability_manifest() -> dict[str, Any]:
    manifest = _capability_manifest()
    family_counts = {
        "gpqa_diamond": 12,
        "ifeval": 16,
        "mmlu_pro": 14,
        "tool_json": 10,
        "context": 8,
    }
    family_results = [
        {
            "family": family,
            "planned": count,
            "attempted": count,
            "scored": count,
            "correct": count - 1,
            "failure_counts": {"incorrect": 1, "execution_error": 0, "unscored": 0},
        }
        for family, count in family_counts.items()
    ]
    manifest.update(
        suite="core",
        task_families=list(family_counts),
        family_results=family_results,
        served_model_evidence={
            **manifest["served_model_evidence"],
            "status": "verified_request_binding_without_response_identity",
            "response_instance_id_sha256": None,
            "match": None,
        },
        runtime_evidence={
            **manifest["runtime_evidence"],
            "transport": "evalscope_openai_api",
            "endpoint": "/v1/chat/completions",
        },
        aggregate={
            "planned": 60,
            "attempted": 60,
            "completed": 60,
            "scorable": 60,
            "correct": 55,
            "incorrect": 5,
            "failed": 0,
            "censored": 0,
            "unattempted": 0,
        },
        reasoning_evidence={
            **manifest["reasoning_evidence"],
            "effective_status": "transmitted_not_read_back",
        },
        effective_settings_status="transmitted_not_read_back",
        limitations=[publication._CORE_EVIDENCE_LIMITATION],
    )
    manifest["historical_protocol"]["sample_count"] = 60
    return manifest


def _replace_path(manifest: dict[str, Any], path: tuple[str, ...], replacement: Any) -> None:
    target = manifest
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = replacement


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


@pytest.mark.parametrize(
    "filename", ["brand-mark.svg", "component.astro", "config.mjs", "content.ts"]
)
def test_publication_scan_accepts_reviewable_docs_text_formats(
    tmp_path: Path, filename: str
) -> None:
    candidate = tmp_path / filename
    candidate.write_text(
        "synthetic reviewed documentation source\n",
        encoding="utf-8",
    )
    policies = _policies()
    policies["publication"]["allowed_suffixes"].append(candidate.suffix)

    assert publication._scan_file(candidate, tmp_path, policies) == []


def test_publication_scan_detects_private_path_without_echoing_it(tmp_path: Path) -> None:
    private_path_sentinel = "/" + "Users/synthetic-operator/private-project/result.json"
    candidate = tmp_path / "report.md"
    candidate.write_text(f"Private location: {private_path_sentinel}\n", encoding="utf-8")

    findings = publication._scan_file(candidate, tmp_path, _policies())

    assert any(finding["rule"] == "absolute-user-path" for finding in findings)
    serialized = repr(findings)
    assert private_path_sentinel not in serialized
    assert all(finding.get("matched_text_redacted") is True for finding in findings)


@pytest.mark.parametrize(
    ("filename", "size", "blocked"),
    [
        ("uv.lock", 1_000_001, False),
        ("uv.lock", 1_100_000, False),
        ("uv.lock", 1_100_001, True),
        ("nested/uv.lock", 1_000_001, True),
        ("other.lock", 1_000_001, True),
        ("UV.LOCK", 1_000_001, True),
        ("report.md", 1_000_001, True),
    ],
)
def test_publication_size_allowance_is_only_for_exact_root_lockfile(
    tmp_path: Path, filename: str, size: int, blocked: bool
) -> None:
    policies = _policies()
    policies["publication"].update(max_file_bytes=1_000_000, max_root_uv_lock_bytes=1_100_000)
    policies["publication"]["allowed_suffixes"].append(".lock")
    candidate = tmp_path / filename
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_bytes(b"#" * size)

    findings = publication._scan_file(candidate, tmp_path, policies)

    assert bool(findings) is blocked
    assert all(finding["rule"] == "file-too-large" for finding in findings)


def test_publication_root_lockfile_keeps_default_limit_without_opt_in(tmp_path: Path) -> None:
    candidate = tmp_path / "uv.lock"
    candidate.write_bytes(b"#" * 100_001)

    findings = publication._scan_file(candidate, tmp_path, _policies())

    assert findings == [{"rule": "file-too-large", "path": "uv.lock", "severity": "block"}]


def test_publication_large_root_lockfile_still_scans_content_to_end(tmp_path: Path) -> None:
    policies = _policies()
    policies["publication"].update(max_file_bytes=1_000_000, max_root_uv_lock_bytes=1_100_000)
    policies["publication"]["allowed_suffixes"].append(".lock")
    sentinel = "/" + "Users/synthetic-operator/private-project/result.json"
    candidate = tmp_path / "uv.lock"
    candidate.write_text("#" * 1_000_001 + "\n" + sentinel, encoding="utf-8")

    findings = publication._scan_file(candidate, tmp_path, policies)

    assert any(finding["rule"] == "absolute-user-path" for finding in findings)
    assert not any(finding["rule"] == "file-too-large" for finding in findings)
    assert sentinel not in repr(findings)


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
    manifest = _capability_manifest()
    manifest.update(
        {
            "private_task_manifest": [{"prompt": "must not export"}],
            "raw_response": {"content": "must not export"},
            "instance_id": "must-not-export",
            "started_at": "2026-09-19T00:00:00Z",
            "local_path": "/" + "Users/synthetic-operator/private",
            "sandbox_private_logs": [{"stdout": "private-sandbox-output"}],
            "sandbox_command": ["docker", "run", "private-container-id"],
            "generated_code": "private-generated-code",
            "hidden_tests": ["private-hidden-test"],
        }
    )
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
    assert preview["publication_purpose"] == "held_out_model_capability"
    assert preview["capability_evidence"] is True
    assert set(preview["coding_sandbox"]) == {
        "qualification",
        "attestation_file_sha256",
        "run_fingerprint_sha256",
        "frozen_before_execution",
        "planned_case_count",
        "attempted_case_count",
        "denominator_count",
        "denominator_policy",
    }
    assert set(preview["coding_sandbox"]["qualification"]) == {
        "schema_version",
        "qualified",
        "attestation_revision",
        "image_digest",
        "policy_sha256",
        "qualification_event_count",
    }
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
        "private-sandbox-output",
        "private-container-id",
        "private-generated-code",
        "private-hidden-test",
    ):
        assert forbidden not in serialized


def test_complete_core_manifest_is_capability_publication_eligible(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manifest = _core_capability_manifest()
    monkeypatch.setattr(publication, "load_run", lambda run_id, root=None: (manifest, []))
    monkeypatch.setattr(
        publication,
        "audit_publication",
        lambda **kwargs: {"status": "pass", "findings": []},
    )

    result = prepare_publication("synthetic-core-run", dry_run=True, root=tmp_path)

    assert result["status"] == "dry_run_ready_for_human_review"
    assert result["preview"]["publication_purpose"] == "held_out_model_capability"
    assert publication._capability_publication_failures(manifest) == []


def test_core_publication_rejects_fabricated_response_identity_or_reasoning_acceptance() -> None:
    manifest = _core_capability_manifest()
    manifest["served_model_evidence"].update(
        response_instance_id_sha256=manifest["served_model_evidence"][
            "requested_instance_id_sha256"
        ],
        match=True,
    )
    manifest["reasoning_evidence"]["effective_status"] = "accepted_by_runtime"
    manifest["effective_settings_status"] = "accepted_by_runtime_not_read_back"

    failures = publication._capability_publication_failures(manifest)

    assert "served_model_evidence.match" in failures
    assert "served_model_evidence.response_instance_id_sha256" in failures
    assert "reasoning_evidence.effective_status" in failures
    assert "effective_settings_status" in failures


@pytest.mark.parametrize("mode", ["incomplete", "mock", "calibration"])
def test_incomplete_or_noncapability_core_manifest_is_rejected(mode: str) -> None:
    manifest = _core_capability_manifest()
    if mode == "incomplete":
        manifest["family_results"].pop()
    elif mode == "mock":
        manifest["evidence_class"] = "synthetic_mock"
    else:
        manifest["held_out"] = False
        manifest["selection_status"] = "calibration"

    assert publication._publication_purpose(manifest) is None
    if mode == "incomplete":
        assert "family_results" in publication._capability_publication_failures(manifest)


@pytest.mark.parametrize(
    ("path", "replacement", "expected_field"),
    [
        (("status",), "partial", "status"),
        (("aggregate", "attempted"), 2, "aggregate.attempted"),
        (("aggregate", "censored"), 1, "aggregate.censored"),
        (("locality_evidence", "status"), "remote", "locality_evidence.status"),
        (("served_model_evidence", "match"), False, "served_model_evidence.match"),
        (
            ("model_instance_evidence", "instance_id_sha256"),
            "f" * 64,
            "served_model_evidence.requested_instance_id_sha256",
        ),
        (("runtime_evidence", "engine_version"), None, "runtime_evidence.engine_version"),
        (
            ("selection_evidence", "manifest_source"),
            "builtin",
            "selection_evidence.manifest_source",
        ),
        (
            ("selection_evidence", "frozen_before_tuning"),
            False,
            "selection_evidence.frozen_before_tuning",
        ),
        (
            ("selection_evidence", "contamination_review_revision"),
            None,
            "selection_evidence.contamination_review_revision",
        ),
        (
            ("historical_protocol", "benchmark_version"),
            None,
            "historical_protocol.benchmark_version",
        ),
        (
            ("historical_protocol", "dataset_revision"),
            "unresolved",
            "historical_protocol.dataset_revision",
        ),
        (("historical_protocol", "split"), None, "historical_protocol.split"),
        (
            ("historical_protocol", "sample_id_manifest"),
            "e" * 64,
            "selection_evidence.ordered_sample_manifest_sha256",
        ),
        (
            ("historical_protocol", "prompts_or_template_revision"),
            None,
            "historical_protocol.prompts_or_template_revision",
        ),
        (
            ("historical_protocol", "attempts_per_task"),
            2,
            "historical_protocol.attempts_per_task",
        ),
        (
            ("historical_protocol", "failure_policy"),
            "drop_failures",
            "historical_protocol.failure_policy",
        ),
        (
            ("historical_protocol", "denominator"),
            "successful_only",
            "historical_protocol.denominator",
        ),
        (
            ("scorer_evidence", "content_sha256"),
            "not-a-hash",
            "scorer_evidence.content_sha256",
        ),
        (
            ("scorer_evidence", "namespace"),
            "harness-only",
            "scorer_evidence.namespace",
        ),
        (
            ("scorer_evidence", "evidence_class"),
            "synthetic_mock",
            "scorer_evidence.evidence_class",
        ),
        (
            ("scorer_evidence", "eligible_for_capability_report"),
            False,
            "scorer_evidence.eligible_for_capability_report",
        ),
        (
            ("scorer_evidence", "calibration", "status"),
            "harness_only",
            "scorer_evidence.calibration.status",
        ),
        (
            ("scorer_evidence", "calibration", "independent_from_evaluation"),
            False,
            "scorer_evidence.calibration.independent_from_evaluation",
        ),
        (("selection_status",), "post_hoc_exploratory", "selection_status"),
        (
            ("calibration_heldout_separation",),
            "post_hoc_exploratory",
            "calibration_heldout_separation",
        ),
        (("sandbox_policy",), {}, "sandbox_policy"),
        (("sandbox_attestation",), {}, "sandbox_attestation"),
        (
            ("sandbox_attestation", "network_mode"),
            "bridge",
            "sandbox_attestation",
        ),
        (
            ("sandbox_qualification", "attestation_revision"),
            "different-revision",
            "sandbox_qualification.attestation_revision",
        ),
        (
            ("sandbox_qualification", "qualification_event_count"),
            0,
            "sandbox_qualification.qualification_event_count",
        ),
        (
            ("sandbox_attestation_sha256",),
            "not-a-hash",
            "coding_run_binding.attestation_file_sha256",
        ),
        (
            ("coding_run_binding", "policy_sha256"),
            "0" * 64,
            "coding_run_binding.policy_sha256",
        ),
        (
            ("coding_run_binding", "attestation_revision"),
            "different-revision",
            "coding_run_binding.attestation_revision",
        ),
        (
            ("coding_run_binding", "run_fingerprint_sha256"),
            "7" * 64,
            "coding_run_binding.run_fingerprint_sha256",
        ),
        (
            ("coding_run_binding", "attempted_case_count"),
            0,
            "coding_run_binding.case_denominator",
        ),
        (
            ("coding_run_binding", "denominator_count"),
            0,
            "coding_run_binding.case_denominator",
        ),
    ],
)
def test_capability_export_fails_closed_when_evidence_is_incomplete(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    path: tuple[str, ...],
    replacement: Any,
    expected_field: str,
) -> None:
    manifest = deepcopy(_capability_manifest())
    _replace_path(manifest, path, replacement)
    monkeypatch.setattr(publication, "load_run", lambda run_id, root=None: (manifest, []))
    monkeypatch.setattr(
        publication,
        "audit_publication",
        lambda **kwargs: {"status": "pass", "findings": []},
    )

    result = prepare_publication("synthetic-run", dry_run=True, root=tmp_path)

    assert result["status"] == "dry_run_blocked"
    assert result["preview"]["publication_purpose"] == "not_eligible"
    assert result["preview"]["capability_evidence"] is False
    assert any(
        blocker.get("rule") == "capability-publication-evidence-incomplete"
        and blocker.get("field") == expected_field
        for blocker in result["blockers"]
    )


def test_skeletal_held_out_manifest_is_not_capability_evidence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manifest = {
        "evidence_class": "local_measurement",
        "model_is_splash": True,
        "suite": "pilot",
        "held_out": True,
    }
    monkeypatch.setattr(publication, "load_run", lambda run_id, root=None: (manifest, []))
    monkeypatch.setattr(
        publication,
        "audit_publication",
        lambda **kwargs: {"status": "pass", "findings": []},
    )

    result = prepare_publication("synthetic-run", dry_run=True, root=tmp_path)

    assert result["status"] == "dry_run_blocked"
    assert result["preview"]["capability_evidence"] is False
    assert any(
        blocker.get("rule") == "capability-publication-evidence-incomplete"
        for blocker in result["blockers"]
    )


def test_private_sandbox_fields_are_rejected_from_sanitized_qualification(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manifest = _capability_manifest()
    manifest["sandbox_qualification"]["private_logs"] = [
        {"command": "private-command", "container_id": "private-container"}
    ]
    monkeypatch.setattr(publication, "load_run", lambda run_id, root=None: (manifest, []))
    monkeypatch.setattr(
        publication,
        "audit_publication",
        lambda **kwargs: {"status": "pass", "findings": []},
    )

    result = prepare_publication("synthetic-run", dry_run=True, root=tmp_path)

    assert result["status"] == "dry_run_blocked"
    assert result["preview"]["capability_evidence"] is False
    assert any(
        blocker.get("field") == "sandbox_qualification.schema" for blocker in result["blockers"]
    )
    assert "private-command" not in repr(result["preview"])
    assert "private-container" not in repr(result["preview"])


def test_public_export_omits_raw_run_id_from_payload_and_markdown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    raw_run_id = "splash-pilot-20260919T204522Z"
    manifest = _capability_manifest()
    manifest["limitations"] = [raw_run_id]
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


def test_post_hoc_pilot_stages_only_runtime_scorer_qualification(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    raw_run_id = "post-hoc-pilot-private-run-id"
    manifest = {
        "evidence_class": "local_measurement",
        "model_is_splash": True,
        "suite": "pilot",
        "held_out": False,
        "selection_status": "post_hoc_exploratory",
        "calibration_heldout_separation": "post_hoc_exploratory",
        "protocol": {"task_set": "reused-pilot", "scorer_version": "synthetic-v1"},
        "selection_hash": "synthetic-selection-hash",
        "aggregate": {
            "planned": 3,
            "attempted": 3,
            "completed": 3,
            "scorable": 3,
            "failed": 0,
            "censored": 0,
            "unattempted": 0,
            "end_to_end_deployment_success": {"rate": 1.0, "denominator": 3},
            "capability_conditional_on_valid_execution": {
                "rate": 1.0,
                "denominator": 3,
            },
            "frontier_delta": 99,
        },
        "primary_objective_status_if_run": "pilot_only",
        "historical_comparison_status": "matched",
        "raw_response": {"content": "must not export"},
        "instance_id": "private-runtime-id",
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

    assert payload["publication_purpose"] == "post_hoc_runtime_scorer_qualification"
    assert payload["capability_evidence"] is False
    assert payload["primary_objective_status"] == "blocked"
    assert (
        payload["historical_comparison_status"]
        == "prohibited for post-hoc runtime/scorer qualification evidence"
    )
    assert "capability_conditional_on_valid_execution" not in payload["aggregate"]
    assert "frontier_delta" not in payload["aggregate"]
    serialized = repr(payload) + markdown
    for forbidden in (raw_run_id, "must not export", "private-runtime-id", "matched"):
        assert forbidden not in serialized
    assert "Capability evidence: `false`" in markdown


def test_post_hoc_pilot_requires_both_explicit_exploratory_markers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manifest = {
        "evidence_class": "local_measurement",
        "model_is_splash": True,
        "suite": "pilot",
        "held_out": False,
        "selection_status": "post_hoc_exploratory",
        "protocol": {"task_set": "reused-pilot", "scorer_version": "synthetic-v1"},
        "aggregate": {"completed": 3},
    }
    monkeypatch.setattr(publication, "load_run", lambda run_id, root=None: (manifest, []))
    monkeypatch.setattr(
        publication,
        "audit_publication",
        lambda **kwargs: {"status": "pass", "findings": []},
    )

    result = prepare_publication("ambiguous-post-hoc-run", dry_run=True, root=tmp_path)

    assert result["status"] == "dry_run_blocked"
    assert result["preview"]["publication_purpose"] == "not_eligible"
    assert result["preview"]["capability_evidence"] is False
    assert any(
        blocker["rule"] == "non-held-out-result-not-capability-evidence"
        for blocker in result["blockers"]
    )
