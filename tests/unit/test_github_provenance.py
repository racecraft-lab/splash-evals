from __future__ import annotations

import copy
import importlib.util
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

import local_evals.publication as publication

_COLLECTOR_PATH = Path(__file__).resolve().parents[2] / "scripts" / "collect_github_provenance.py"
_COLLECTOR_SPEC = importlib.util.spec_from_file_location(
    "collect_github_provenance", _COLLECTOR_PATH
)
assert _COLLECTOR_SPEC is not None and _COLLECTOR_SPEC.loader is not None
collector = importlib.util.module_from_spec(_COLLECTOR_SPEC)
_COLLECTOR_SPEC.loader.exec_module(collector)


REPOSITORY = "racecraft-lab/splash-evals"
HISTORICAL_DEPENDABOT_SHA = "3a4070415d7a7a57251093170ffbbf46f6f5bcb4"
AUTOMATION_NAME = "Racecraft Lab Automation"
AUTOMATION_EMAIL = "info@racecraft.co"
AUTHOR_EMAIL = "fgabelmannjr" + "@" + "users.noreply.github.com"
COMMITTER_EMAIL = "noreply" + "@github.com"
AUTHOR = {
    "name": "Approved Web Actor",
    "email": AUTHOR_EMAIL,
    "login": "fgabelmannjr",
}
COMMITTER = {"name": "GitHub", "email": COMMITTER_EMAIL, "login": "web-flow"}
COMMITTER_POLICY = {"name": "GitHub", "login": "web-flow"}
CHECK = {
    "name": "final gate",
    "app_slug": "github-actions",
    "status": "completed",
    "conclusion": "success",
}


def _policies() -> dict[str, Any]:
    return {
        "publication": {
            "max_file_bytes": 100_000,
            "allowed_suffixes": [".md", ".py", ".json", ".yaml", ".yml", ".toml"],
            "denied_suffixes": [".zip", ".tar", ".gz", ".ipynb", ".sqlite", ".db"],
            "automation_identity": {"name": AUTOMATION_NAME, "email": AUTOMATION_EMAIL},
            "github_squash_provenance": {
                "repository": REPOSITORY,
                "ref": "refs/heads/main",
                "actor_login": "fgabelmannjr",
                "committer": COMMITTER_POLICY,
                "required_check": CHECK,
            },
        }
    }


def _init_repo(tmp_path: Path):
    git = shutil.which("git")
    assert git is not None

    def run_git(
        *args: str,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        process_env = os.environ.copy()
        if env:
            process_env.update(env)
        return subprocess.run(  # noqa: S603 - resolved executable and fixed test arguments.
            [git, *args],
            cwd=tmp_path,
            check=True,
            capture_output=True,
            text=True,
            input=input_text,
            env=process_env,
        )

    run_git("init", "-b", "main")
    run_git("config", "--local", "user.name", AUTOMATION_NAME)
    run_git("config", "--local", "user.email", AUTOMATION_EMAIL)
    run_git("config", "--local", "commit.gpgsign", "false")
    (tmp_path / "record.md").write_text("automation base\n", encoding="utf-8")
    run_git("add", "record.md")
    run_git("commit", "-m", "Automation base")
    return run_git


def _platform_commit(run_git: Any, message: str) -> tuple[str, str]:
    tree = run_git("write-tree").stdout.strip()
    parent = run_git("rev-parse", "HEAD").stdout.strip()
    result = run_git(
        "commit-tree",
        tree,
        "-p",
        parent,
        input_text=message,
        env={
            "GIT_AUTHOR_NAME": AUTHOR["name"],
            "GIT_AUTHOR_EMAIL": AUTHOR["email"],
            "GIT_COMMITTER_NAME": COMMITTER["name"],
            "GIT_COMMITTER_EMAIL": COMMITTER["email"],
            "GIT_AUTHOR_DATE": "2026-09-19T00:00:00Z",
            "GIT_COMMITTER_DATE": "2026-09-19T00:00:00Z",
        },
    )
    commit_sha = result.stdout.strip()
    run_git("update-ref", "refs/heads/main", commit_sha)
    return commit_sha, tree


def _set_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PUBLIC_GIT_AUTHOR_NAME", AUTOMATION_NAME)
    monkeypatch.setenv("PUBLIC_GIT_AUTHOR_EMAIL", AUTOMATION_EMAIL)
    monkeypatch.delenv("GITHUB_REF", raising=False)
    for variable in (
        "GITHUB_RUN_ID",
        "GITHUB_RUN_ATTEMPT",
        "GITHUB_EVENT_NAME",
        "GITHUB_WORKFLOW_REF",
    ):
        monkeypatch.delenv(variable, raising=False)


def _record(commit_sha: str, tree_sha: str) -> dict[str, Any]:
    head_sha = "b" * 40
    return {
        "commit_sha": commit_sha,
        "repository": REPOSITORY,
        "ref": "refs/heads/main",
        "actor_login": "fgabelmannjr",
        "author": copy.deepcopy(AUTHOR),
        "committer": copy.deepcopy(COMMITTER),
        "verification": {"verified": True, "reason": "valid"},
        "associated_pr_count": 1,
        "main_binding": {
            "contained": True,
            "main_tip_sha": "c" * 40,
            "status": "ahead",
            "base_sha": commit_sha,
            "merge_base_sha": commit_sha,
        },
        "pull_request": {
            "number": 7,
            "merged_at": "2026-09-19T00:00:00Z",
            "merge_commit_sha": commit_sha,
            "base_repository": REPOSITORY,
            "base_ref": "main",
            "user_login": "fgabelmannjr",
            "head_sha": head_sha,
            "head_tree_sha": tree_sha,
            "merge_tree_sha": tree_sha,
            "required_check_count": 1,
            "required_check": {**CHECK, "head_sha": head_sha},
        },
    }


def _dependabot_record(commit_sha: str, tree_sha: str) -> dict[str, Any]:
    record = _record(commit_sha, tree_sha)
    record["actor_login"] = "dependabot[bot]"
    record["author"].update(
        {
            "name": "dependabot[bot]",
            "email": "49699333+dependabot[bot]@users.noreply.github.com",
            "login": "dependabot[bot]",
        }
    )
    record["pull_request"]["user_login"] = "dependabot[bot]"
    return record


def _evidence(head_sha: str, records: list[dict[str, Any]], *, ref: str = "refs/heads/main"):
    return {
        "schema_version": 1,
        "repository": REPOSITORY,
        "ref": ref,
        "head_sha": head_sha,
        "records": records,
    }


def _write_evidence(tmp_path: Path, document: dict[str, Any]) -> Path:
    path = tmp_path.parent / f"{tmp_path.name}-github-provenance.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _identity_audit(
    monkeypatch: pytest.MonkeyPatch,
    repo: Path,
    policies: dict[str, Any],
    evidence: Path | None = None,
    *,
    ref: str = "refs/heads/main",
    context: dict[str, str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    _set_identity(monkeypatch)
    monkeypatch.setenv("GITHUB_REF", ref)
    if context is not None:
        for key, value in context.items():
            monkeypatch.setenv(key, value)
    return publication._identity_findings(repo, policies, provenance_path=evidence)


def test_platform_identity_without_evidence_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run_git = _init_repo(tmp_path)
    _platform_commit(run_git, "Platform commit without proof")

    findings, _ = _identity_audit(monkeypatch, tmp_path, _policies())
    rules = {finding["rule"] for finding in findings}

    assert "history-identity-mismatch" in rules
    assert "github-squash-provenance-required" in rules


def test_complete_valid_squash_evidence_allows_platform_identity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run_git = _init_repo(tmp_path)
    commit_sha, tree_sha = _platform_commit(run_git, "Platform commit with proof")
    evidence = _write_evidence(tmp_path, _evidence(commit_sha, [_record(commit_sha, tree_sha)]))

    findings, summary = _identity_audit(monkeypatch, tmp_path, _policies(), evidence)

    assert findings == []
    assert summary["github_provenance_records"] == 1


def test_legacy_policy_without_actor_overrides_uses_default_actor() -> None:
    policy = _policies()["publication"]["github_squash_provenance"]

    fields = publication._github_policy_fields(policy)

    assert fields is not None
    assert fields[0] == "fgabelmannjr"
    assert fields[1] == {}
    assert publication._github_actor_login(fields[0], fields[1], "d" * 40) == "fgabelmannjr"


def test_offline_audit_allows_dependabot_only_for_the_exact_approved_sha() -> None:
    record = _dependabot_record(HISTORICAL_DEPENDABOT_SHA, "b" * 40)

    validated = publication._validate_provenance_record(
        record,
        repository=REPOSITORY,
        policy_ref="refs/heads/main",
        actor_login="fgabelmannjr",
        actor_overrides={HISTORICAL_DEPENDABOT_SHA: "dependabot[bot]"},
        committer_policy=COMMITTER_POLICY,
        check_policy=CHECK,
    )

    assert validated is not None
    assert validated[0] == HISTORICAL_DEPENDABOT_SHA


@pytest.mark.parametrize("failure", ["other_sha", "other_author"])
def test_offline_audit_rejects_dependabot_for_other_sha_or_author(failure: str) -> None:
    commit_sha = HISTORICAL_DEPENDABOT_SHA
    record = _dependabot_record(commit_sha, "b" * 40)
    if failure == "other_sha":
        commit_sha = "d" * 40
        record["commit_sha"] = commit_sha
        record["pull_request"]["merge_commit_sha"] = commit_sha
        record["main_binding"]["base_sha"] = commit_sha
        record["main_binding"]["merge_base_sha"] = commit_sha
    else:
        record["author"]["login"] = "other-actor"

    validated = publication._validate_provenance_record(
        record,
        repository=REPOSITORY,
        policy_ref="refs/heads/main",
        actor_login="fgabelmannjr",
        actor_overrides={HISTORICAL_DEPENDABOT_SHA: "dependabot[bot]"},
        committer_policy=COMMITTER_POLICY,
        check_policy=CHECK,
    )

    assert validated is None


def test_collector_resolves_only_the_exact_historical_actor_sha() -> None:
    policy = collector._policy(Path(__file__).resolve().parents[2])
    actor_login = policy[5]
    actor_overrides = policy[6]

    assert actor_overrides == {HISTORICAL_DEPENDABOT_SHA: "dependabot[bot]"}
    assert (
        collector._github_actor_login(actor_login, actor_overrides, HISTORICAL_DEPENDABOT_SHA)
        == "dependabot[bot]"
    )
    assert collector._github_actor_login(actor_login, actor_overrides, "d" * 40) == actor_login


@pytest.mark.parametrize(
    ("field", "value"),
    [("name", "Unrelated API identity"), ("email", "forged" + "@" + "example.invalid")],
)
def test_evidence_identity_must_match_local_commit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, field: str, value: str
) -> None:
    run_git = _init_repo(tmp_path)
    commit_sha, tree_sha = _platform_commit(run_git, "Platform commit with mismatched identity")
    document = _evidence(commit_sha, [_record(commit_sha, tree_sha)])
    document["records"][0]["author"][field] = value
    evidence = _write_evidence(tmp_path, document)

    findings, _ = _identity_audit(monkeypatch, tmp_path, _policies(), evidence)
    rules = {finding["rule"] for finding in findings}

    assert "history-identity-mismatch" in rules
    assert "github-squash-provenance-invalid" in rules


def test_github_identity_email_remains_blocked_in_tracked_source(
    tmp_path: Path,
) -> None:
    path = tmp_path / "record.md"
    path.write_text(AUTHOR_EMAIL, encoding="utf-8")

    findings = publication._scan_file(path, tmp_path, _policies())

    assert any(finding["rule"] == "email-address" for finding in findings)


def test_evidence_context_is_schema_validated_and_bound_to_actions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run_git = _init_repo(tmp_path)
    commit_sha, tree_sha = _platform_commit(run_git, "Platform commit with Actions context")
    document = _evidence(commit_sha, [_record(commit_sha, tree_sha)])
    document["context"] = {
        "run_id": 123,
        "run_attempt": 2,
        "event_name": "push",
        "workflow_ref": "racecraft-lab/splash-evals/.github/workflows/ci.yml@refs/heads/main",
    }
    evidence = _write_evidence(tmp_path, document)
    context = {
        "GITHUB_RUN_ID": "123",
        "GITHUB_RUN_ATTEMPT": "2",
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_WORKFLOW_REF": (
            "racecraft-lab/splash-evals/.github/workflows/ci.yml@refs/heads/main"
        ),
    }

    findings, _ = _identity_audit(monkeypatch, tmp_path, _policies(), evidence, context=context)

    assert findings == []


def test_evidence_context_mismatch_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run_git = _init_repo(tmp_path)
    commit_sha, tree_sha = _platform_commit(run_git, "Platform commit with stale context")
    document = _evidence(commit_sha, [_record(commit_sha, tree_sha)])
    document["context"] = {
        "run_id": 123,
        "run_attempt": 1,
        "event_name": "push",
        "workflow_ref": "workflow@refs/heads/main",
    }
    evidence = _write_evidence(tmp_path, document)
    context = {
        "GITHUB_RUN_ID": "124",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_WORKFLOW_REF": "workflow@refs/heads/main",
    }

    findings, _ = _identity_audit(monkeypatch, tmp_path, _policies(), evidence, context=context)

    assert any(finding["rule"] == "github-provenance-invalid" for finding in findings)


def test_evidence_context_is_required_when_actions_context_is_present(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run_git = _init_repo(tmp_path)
    commit_sha, tree_sha = _platform_commit(run_git, "Platform commit without context")
    evidence = _write_evidence(tmp_path, _evidence(commit_sha, [_record(commit_sha, tree_sha)]))
    context = {
        "GITHUB_RUN_ID": "123",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_WORKFLOW_REF": "workflow@refs/heads/main",
    }

    findings, _ = _identity_audit(monkeypatch, tmp_path, _policies(), evidence, context=context)

    assert any(
        finding["rule"] in {"github-provenance-invalid", "github-squash-provenance-invalid"}
        for finding in findings
    )


def test_evidence_inside_checkout_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run_git = _init_repo(tmp_path)
    commit_sha, tree_sha = _platform_commit(run_git, "Evidence inside checkout")
    inside = tmp_path / "github-provenance.json"
    inside.write_text(
        json.dumps(_evidence(commit_sha, [_record(commit_sha, tree_sha)])), encoding="utf-8"
    )

    findings, _ = _identity_audit(monkeypatch, tmp_path, _policies(), inside)

    assert any(finding["rule"] == "github-provenance-invalid" for finding in findings)


def test_evidence_symlink_is_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    run_git = _init_repo(tmp_path)
    commit_sha, tree_sha = _platform_commit(run_git, "Symlinked evidence")
    target = _write_evidence(tmp_path, _evidence(commit_sha, [_record(commit_sha, tree_sha)]))
    link = tmp_path.parent / f"{tmp_path.name}-github-provenance-link.json"
    link.symlink_to(target)

    findings, _ = _identity_audit(monkeypatch, tmp_path, _policies(), link)

    assert any(finding["rule"] == "github-provenance-invalid" for finding in findings)


@pytest.mark.parametrize("payload", ["not json", "", "[]"])
def test_malformed_evidence_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, payload: str
) -> None:
    run_git = _init_repo(tmp_path)
    _platform_commit(run_git, "Malformed evidence")
    evidence = tmp_path.parent / f"{tmp_path.name}-malformed.json"
    evidence.write_text(payload, encoding="utf-8")

    findings, _ = _identity_audit(monkeypatch, tmp_path, _policies(), evidence)

    assert any(finding["rule"] == "github-provenance-invalid" for finding in findings)


def test_oversized_evidence_is_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    run_git = _init_repo(tmp_path)
    commit_sha, tree_sha = _platform_commit(run_git, "Oversized evidence")
    evidence = _write_evidence(tmp_path, _evidence(commit_sha, [_record(commit_sha, tree_sha)]))
    with evidence.open("a", encoding="utf-8") as handle:
        handle.write(" " * 1_000_001)

    findings, _ = _identity_audit(monkeypatch, tmp_path, _policies(), evidence)

    assert any(finding["rule"] == "github-provenance-invalid" for finding in findings)


def test_collector_actions_context_is_minimal_and_strict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_RUN_ID", "123")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("GITHUB_WORKFLOW_REF", "workflow@refs/pull/8/merge")

    assert collector._actions_context() == {
        "run_id": 123,
        "run_attempt": 2,
        "event_name": "pull_request",
        "workflow_ref": "workflow@refs/pull/8/merge",
    }

    monkeypatch.delenv("GITHUB_RUN_ATTEMPT")
    with pytest.raises(collector.CollectionError):
        collector._actions_context()


def test_collector_output_must_be_outside_checkout(tmp_path: Path) -> None:
    inside = tmp_path / "evidence.json"

    with pytest.raises(collector.CollectionError):
        collector._write_output(tmp_path, inside, {"schema_version": 1})


@pytest.mark.parametrize(
    "mutate",
    [
        lambda document: document.__setitem__("repository", "other/repo"),
        lambda document: document.__setitem__("ref", "refs/heads/feature"),
        lambda document: document.__setitem__("head_sha", "c" * 40),
        lambda document: document.__setitem__("schema_version", True),
        lambda document: document.__setitem__("unexpected", True),
        lambda document: document["records"][0].__setitem__("unexpected", True),
        lambda document: document["records"].append(copy.deepcopy(document["records"][0])),
        lambda document: document["records"][0].__setitem__("actor_login", "other-actor"),
        lambda document: document["records"][0]["author"].__setitem__(
            "email", "other" + "@example.invalid"
        ),
        lambda document: document["records"][0]["verification"].__setitem__("reason", "unsigned"),
        lambda document: document["records"][0].__setitem__("associated_pr_count", True),
        lambda document: document["records"][0]["pull_request"].__setitem__(
            "required_check_count", True
        ),
        lambda document: document["records"][0]["pull_request"].__setitem__(
            "merge_commit_sha", "d" * 40
        ),
        lambda document: document["records"][0]["pull_request"].__setitem__(
            "base_repository", "other/repo"
        ),
        lambda document: document["records"][0]["pull_request"].__setitem__("base_ref", "feature"),
        lambda document: document["records"][0]["pull_request"].__setitem__(
            "merge_tree_sha", "e" * 40
        ),
        lambda document: document["records"][0]["pull_request"]["required_check"].__setitem__(
            "conclusion", "failure"
        ),
        lambda document: document["records"][0]["pull_request"]["required_check"].__setitem__(
            "head_sha", "f" * 40
        ),
        lambda document: document["records"][0]["main_binding"].__setitem__("contained", False),
        lambda document: document["records"][0]["main_binding"].__setitem__(
            "main_tip_sha", "a" * 41
        ),
        lambda document: document["records"][0]["main_binding"].__setitem__("status", "behind"),
        lambda document: document["records"][0]["main_binding"].__setitem__("base_sha", "a" * 40),
        lambda document: document["records"][0]["main_binding"].__setitem__(
            "merge_base_sha", "b" * 40
        ),
    ],
    ids=[
        "repository",
        "ref",
        "head-sha",
        "schema-version-bool",
        "top-level-extra-key",
        "record-extra-key",
        "duplicate-record",
        "actor",
        "identity",
        "signature",
        "associated-pr-count",
        "required-check-count",
        "merge-sha",
        "base-repository",
        "base-ref",
        "tree",
        "check",
        "check-head-sha",
        "main-containment",
        "main-tip-sha",
        "main-status",
        "main-base-sha",
        "main-merge-base-sha",
    ],
)
def test_forged_or_mismatched_evidence_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mutate: Any
) -> None:
    run_git = _init_repo(tmp_path)
    commit_sha, tree_sha = _platform_commit(run_git, "Platform commit with forged proof")
    document = _evidence(commit_sha, [_record(commit_sha, tree_sha)])
    mutate(document)
    evidence = _write_evidence(tmp_path, document)

    findings, _ = _identity_audit(monkeypatch, tmp_path, _policies(), evidence)

    assert any(
        finding["rule"] in {"github-provenance-invalid", "github-squash-provenance-invalid"}
        for finding in findings
    )


def test_evidence_cannot_exempt_an_arbitrary_ancestor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run_git = _init_repo(tmp_path)
    first_sha, first_tree = _platform_commit(run_git, "First platform commit")
    second_sha, second_tree = _platform_commit(run_git, "Second platform commit")
    evidence = _write_evidence(tmp_path, _evidence(second_sha, [_record(second_sha, second_tree)]))

    findings, _ = _identity_audit(monkeypatch, tmp_path, _policies(), evidence)

    assert any(finding["rule"] == "github-squash-provenance-required" for finding in findings)
    assert first_sha != second_sha
    assert first_tree == second_tree


def test_previous_merged_ancestor_is_valid_in_pr_context(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run_git = _init_repo(tmp_path)
    commit_sha, tree_sha = _platform_commit(run_git, "Previously merged platform commit")
    (tmp_path / "record.md").write_text("automation PR head\n", encoding="utf-8")
    run_git("add", "record.md")
    run_git("commit", "-m", "Automation PR head")
    head_sha = run_git("rev-parse", "HEAD").stdout.strip()
    evidence = _write_evidence(
        tmp_path, _evidence(head_sha, [_record(commit_sha, tree_sha)], ref="refs/pull/8/head")
    )

    findings, _ = _identity_audit(
        monkeypatch, tmp_path, _policies(), evidence, ref="refs/pull/8/head"
    )

    assert findings == []


def test_new_platform_pr_head_without_merged_provenance_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run_git = _init_repo(tmp_path)
    previous_sha, previous_tree = _platform_commit(run_git, "Previously merged platform commit")
    current_sha, _ = _platform_commit(run_git, "New unmerged platform commit")
    evidence = _write_evidence(
        tmp_path,
        _evidence(current_sha, [_record(previous_sha, previous_tree)], ref="refs/pull/9/head"),
    )

    findings, _ = _identity_audit(
        monkeypatch, tmp_path, _policies(), evidence, ref="refs/pull/9/head"
    )

    assert any(finding["rule"] == "github-squash-provenance-required" for finding in findings)


def test_collector_rejects_multiple_associated_pull_requests(tmp_path: Path) -> None:
    commit_sha = "a" * 40
    tree_sha = "b" * 40

    def request(endpoint: str) -> Any:
        if endpoint.endswith(f"commits/{commit_sha}"):
            return {
                "sha": commit_sha,
                "commit": {
                    "author": {"name": AUTHOR["name"], "email": AUTHOR["email"]},
                    "committer": {"name": COMMITTER["name"], "email": COMMITTER["email"]},
                    "tree": {"sha": tree_sha},
                    "verification": {"verified": True, "reason": "valid"},
                },
                "author": {"login": AUTHOR["login"]},
                "committer": {"login": COMMITTER["login"]},
            }
        if endpoint.endswith(f"commits/{commit_sha}/pulls?per_page=100"):
            return [{"number": 7}, {"number": 8}]
        raise AssertionError(endpoint)

    with pytest.raises(collector.CollectionError):
        collector._commit_record(
            REPOSITORY,
            commit_sha,
            committer_policy=COMMITTER_POLICY,
            check_policy=CHECK,
            actor_login="fgabelmannjr",
            request=request,
        )


@pytest.mark.parametrize(
    ("status", "main_tip_sha", "base_sha", "merge_base_sha", "valid"),
    [
        ("ahead", "b" * 40, "a" * 40, "a" * 40, True),
        ("identical", "a" * 40, "a" * 40, "a" * 40, True),
        ("behind", "b" * 40, "a" * 40, "a" * 40, False),
        ("diverged", "b" * 40, "a" * 40, "a" * 40, False),
        ("behind", "b" * 40, "c" * 40, "a" * 40, False),
        ("behind", "b" * 40, "a" * 40, "c" * 40, False),
    ],
    ids=[
        "ancestor-ahead",
        "tip-identical",
        "behind",
        "diverged",
        "wrong-base",
        "wrong-merge-base",
    ],
)
def test_collector_requires_current_main_containment(
    status: str,
    main_tip_sha: str,
    base_sha: str,
    merge_base_sha: str,
    valid: bool,
) -> None:
    commit_sha = "a" * 40

    def request(endpoint: str) -> Any:
        if endpoint == f"repos/{REPOSITORY}/branches/main":
            return {"name": "main", "commit": {"sha": main_tip_sha}}
        if endpoint == f"repos/{REPOSITORY}/compare/{commit_sha}...{main_tip_sha}":
            return {
                "status": status,
                "base_commit": {"sha": base_sha},
                "merge_base_commit": {"sha": merge_base_sha},
                "commits": [],
            }
        raise AssertionError(endpoint)

    if valid:
        assert collector._main_binding(REPOSITORY, commit_sha, request=request) == {
            "contained": True,
            "main_tip_sha": main_tip_sha,
            "status": status,
            "base_sha": base_sha,
            "merge_base_sha": merge_base_sha,
        }
    else:
        with pytest.raises(collector.CollectionError):
            collector._main_binding(REPOSITORY, commit_sha, request=request)


@pytest.mark.parametrize(
    "branch_response",
    [
        {},
        {"commit": {}},
        {"commit": {"sha": "a" * 41}},
    ],
    ids=["missing-commit", "missing-sha", "invalid-sha"],
)
def test_collector_rejects_malformed_main_binding(branch_response: dict[str, Any]) -> None:
    commit_sha = "a" * 40

    def request(endpoint: str) -> Any:
        if endpoint == f"repos/{REPOSITORY}/branches/main":
            return branch_response
        raise AssertionError(endpoint)

    with pytest.raises(collector.CollectionError):
        collector._main_binding(REPOSITORY, commit_sha, request=request)


def test_collector_output_is_minimal_and_sanitized(tmp_path: Path) -> None:
    commit_sha = "a" * 40
    tree_sha = "b" * 40
    head_sha = "c" * 40
    synthetic_token = "gh" + "p_" + "syntheticsecretvalue"
    private_body = "private pull request body"
    responses: dict[str, Any] = {
        f"repos/{REPOSITORY}/commits/{commit_sha}": {
            "sha": commit_sha,
            "commit": {
                "author": {"name": AUTHOR["name"], "email": AUTHOR["email"]},
                "committer": {"name": COMMITTER["name"], "email": COMMITTER["email"]},
                "tree": {"sha": tree_sha},
                "verification": {
                    "verified": True,
                    "reason": "valid",
                    "signature": synthetic_token,
                },
            },
            "author": {"login": AUTHOR["login"], "name": "extra personal field"},
            "committer": {"login": COMMITTER["login"]},
        },
        f"repos/{REPOSITORY}/commits/{commit_sha}/pulls?per_page=100": [{"number": 7}],
        f"repos/{REPOSITORY}/pulls/7": {
            "number": 7,
            "merged_at": "2026-09-19T00:00:00Z",
            "merge_commit_sha": commit_sha,
            "base": {"ref": "main", "repo": {"full_name": REPOSITORY}},
            "user": {"login": "fgabelmannjr", "name": "private display name"},
            "head": {"sha": head_sha},
            "body": private_body,
            "details_url": "https://example.invalid/private",
        },
        f"repos/{REPOSITORY}/commits/{head_sha}": {
            "commit": {"tree": {"sha": tree_sha}},
        },
        f"repos/{REPOSITORY}/branches/main": {
            "name": "main",
            "commit": {"sha": "d" * 40},
        },
        f"repos/{REPOSITORY}/compare/{commit_sha}...{'d' * 40}": {
            "status": "ahead",
            "base_commit": {"sha": commit_sha},
            "merge_base_commit": {"sha": commit_sha},
            "commits": [],
        },
        f"repos/{REPOSITORY}/commits/{head_sha}/check-runs?per_page=100": {
            "total_count": 1,
            "check_runs": [
                {
                    "name": "final gate",
                    "status": "completed",
                    "conclusion": "success",
                    "app": {"slug": "github-actions"},
                    "details_url": "https://example.invalid/check",
                    "output": {"summary": synthetic_token},
                }
            ],
        },
    }

    record = collector._commit_record(
        REPOSITORY,
        commit_sha,
        committer_policy=COMMITTER_POLICY,
        check_policy=CHECK,
        actor_login="fgabelmannjr",
        request=responses.__getitem__,
    )
    serialized = json.dumps(record, sort_keys=True)

    assert set(record) == {
        "commit_sha",
        "repository",
        "ref",
        "actor_login",
        "author",
        "committer",
        "verification",
        "associated_pr_count",
        "main_binding",
        "pull_request",
    }
    assert private_body not in serialized
    assert synthetic_token not in serialized
    assert "details_url" not in serialized
    assert "signature" not in serialized
    assert record["pull_request"]["required_check"]["head_sha"] == head_sha
    assert record["main_binding"] == {
        "contained": True,
        "main_tip_sha": "d" * 40,
        "status": "ahead",
        "base_sha": commit_sha,
        "merge_base_sha": commit_sha,
    }
