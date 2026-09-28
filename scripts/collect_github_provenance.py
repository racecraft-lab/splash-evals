"""Collect minimal GitHub squash-provenance evidence for the offline audit.

The collector is the only publication-gate component that talks to GitHub. It uses the
GitHub CLI with the read-only token supplied by Actions, extracts only fields consumed by
``local_evals.publication``, and never writes raw API responses or prints command errors.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

from local_evals.publication import (
    PublicationRefused,
    _evidence_path_is_safe,
    _git,
    _github_actor_login,
    _github_actor_overrides,
    _github_context_from_environment,
)


class CollectionError(RuntimeError):
    """The evidence contract could not be established."""


JsonRequest = Callable[[str], Any]


def _nonempty(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise CollectionError("required GitHub field missing")
    return str(value)


def _sha(value: Any) -> str:
    value = _nonempty(value)
    if len(value) not in {40, 64} or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise CollectionError("invalid GitHub object identifier")
    return str(value)


def _mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CollectionError("unexpected GitHub response shape")
    return value


def _actions_context() -> dict[str, Any] | None:
    context = _github_context_from_environment()
    if context == {}:
        raise CollectionError("GitHub Actions context is malformed or incomplete")
    return context


def _policy(
    repo: Path,
) -> tuple[
    dict[str, str],
    dict[str, str],
    dict[str, str],
    str,
    str,
    str,
    dict[str, str],
]:
    try:
        document = yaml.safe_load((repo / "configs" / "policies.yaml").read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise CollectionError("publication policy unavailable") from error
    publication = _mapping(document).get("publication")
    publication = _mapping(publication)
    automation = _mapping(publication.get("automation_identity"))
    provenance = _mapping(publication.get("github_squash_provenance"))
    committer = _mapping(provenance.get("committer"))
    check = _mapping(provenance.get("required_check"))
    actor_overrides = _github_actor_overrides(provenance.get("actor_overrides", {}))
    if actor_overrides is None:
        raise CollectionError("publication actor policy unavailable")
    return (
        {key: _nonempty(automation.get(key)) for key in ("name", "email")},
        {key: _nonempty(committer.get(key)) for key in ("name", "login")},
        {key: _nonempty(check.get(key)) for key in ("name", "app_slug", "status", "conclusion")},
        _nonempty(provenance.get("repository")),
        _nonempty(provenance.get("ref")),
        _nonempty(provenance.get("actor_login")),
        actor_overrides,
    )


def _actor_login_for_commit(
    actor_login: str,
    actor_overrides: dict[str, str],
    commit_sha: str,
) -> str:
    resolved = _github_actor_login(actor_login, actor_overrides, commit_sha)
    if resolved is None:
        raise CollectionError("publication actor policy unavailable")
    return resolved


def _run_git(repo: Path, args: list[str]) -> str:
    try:
        result = _git(repo, args)
    except PublicationRefused as error:
        raise CollectionError("git executable unavailable") from error
    if result.returncode != 0:
        raise CollectionError("local Git history unavailable")
    return result.stdout


def _local_identity(repo: Path, commit_sha: str) -> tuple[str, str, str, str]:
    output = _run_git(
        repo,
        ["show", "-s", "--format=%an%x00%ae%x00%cn%x00%ce", commit_sha],
    ).strip("\n")
    parts = output.split("\x00")
    if len(parts) != 4 or not all(parts):
        raise CollectionError("local commit identity unavailable")
    return parts[0], parts[1], parts[2], parts[3]


def _gh_json(endpoint: str) -> Any:
    executable = shutil.which("gh")
    if executable is None:
        raise CollectionError("GitHub CLI unavailable")
    # API contracts: commit, associated-PR, compare, branch, pull-request, and check-run fields
    # are documented at https://docs.github.com/en/rest/commits/commits,
    # https://docs.github.com/en/rest/commits/commits#compare-two-commits,
    # https://docs.github.com/en/rest/branches/branches,
    result = subprocess.run(  # noqa: S603 - executable and fixed endpoint argument.
        [
            executable,
            "api",
            endpoint,
            "--method",
            "GET",
            "--header",
            "Accept: application/vnd.github+json",
            "--header",
            "X-GitHub-Api-Version: 2022-11-28",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise CollectionError("GitHub API request failed")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise CollectionError("GitHub API returned invalid JSON") from error


def _main_binding(
    repository: str,
    commit_sha: str,
    *,
    request: JsonRequest,
) -> dict[str, Any]:
    branch = _mapping(request(f"repos/{repository}/branches/main"))
    branch_commit = _mapping(branch.get("commit"))
    main_tip_sha = _sha(branch_commit.get("sha"))
    comparison = _mapping(request(f"repos/{repository}/compare/{commit_sha}...{main_tip_sha}"))
    base_commit = _mapping(comparison.get("base_commit"))
    merge_base = _mapping(comparison.get("merge_base_commit"))
    status = comparison.get("status")
    base_sha = _sha(base_commit.get("sha"))
    merge_base_sha = _sha(merge_base.get("sha"))
    if (
        status not in {"ahead", "identical"}
        or base_sha != commit_sha
        or merge_base_sha != commit_sha
    ):
        raise CollectionError("GitHub squash commit is not contained in current main")
    return {
        "contained": True,
        "main_tip_sha": main_tip_sha,
        "status": status,
        "base_sha": base_sha,
        "merge_base_sha": merge_base_sha,
    }


def _commit_identity(
    commit: dict[str, Any],
    commit_sha: str,
    *,
    actor_login: str,
    committer_policy: dict[str, str],
) -> tuple[dict[str, str], dict[str, str], str]:
    if _sha(commit.get("sha")) != commit_sha:
        raise CollectionError("GitHub commit SHA mismatch")
    commit_details = _mapping(commit.get("commit"))
    author = _mapping(commit_details.get("author"))
    committer = _mapping(commit_details.get("committer"))
    author_login = _nonempty(_mapping(commit.get("author")).get("login"))
    committer_login = _nonempty(_mapping(commit.get("committer")).get("login"))
    verification = _mapping(commit_details.get("verification"))
    commit_author = {
        "name": _nonempty(author.get("name")),
        "email": _nonempty(author.get("email")),
        "login": author_login,
    }
    commit_committer = {
        "name": _nonempty(committer.get("name")),
        "email": _nonempty(committer.get("email")),
        "login": committer_login,
    }
    if (
        author_login != actor_login
        or committer_login != committer_policy["login"]
        or committer.get("name") != committer_policy["name"]
        or verification.get("verified") is not True
        or verification.get("reason") != "valid"
    ):
        raise CollectionError("GitHub commit identity or signature is not approved")
    merge_tree_sha = _sha(_mapping(commit_details.get("tree")).get("sha"))
    return commit_author, commit_committer, merge_tree_sha


def _github_commit_record_fields(
    repository: str,
    commit_sha: str,
    local_identity: tuple[str, str, str, str],
    *,
    actor_login: str,
    request: JsonRequest,
    require_verified_signature: bool,
) -> tuple[dict[str, str], dict[str, str], dict[str, Any], str]:
    commit = _mapping(request(f"repos/{repository}/commits/{commit_sha}"))
    if _sha(commit.get("sha")) != commit_sha:
        raise CollectionError("GitHub commit SHA mismatch")
    details = _mapping(commit.get("commit"))
    author = _mapping(details.get("author"))
    committer = _mapping(details.get("committer"))
    author_login = _nonempty(_mapping(commit.get("author")).get("login"))
    committer_login = _nonempty(_mapping(commit.get("committer")).get("login"))
    verification = _mapping(details.get("verification"))
    verified = verification.get("verified")
    reason = _nonempty(verification.get("reason"))
    record_author = {
        "name": _nonempty(author.get("name")),
        "email": _nonempty(author.get("email")),
        "login": author_login,
    }
    record_committer = {
        "name": _nonempty(committer.get("name")),
        "email": _nonempty(committer.get("email")),
        "login": committer_login,
    }
    api_identity = (
        record_author["name"],
        record_author["email"],
        record_committer["name"],
        record_committer["email"],
    )
    if (
        api_identity != local_identity
        or author_login != actor_login
        or (require_verified_signature and (verified is not True or reason != "valid"))
        or type(verified) is not bool
    ):
        raise CollectionError("GitHub commit evidence does not match local history")
    return (
        record_author,
        record_committer,
        {"verified": verified, "reason": reason},
        _sha(_mapping(details.get("tree")).get("sha")),
    )


def _verified_commit_record(
    repository: str,
    commit_sha: str,
    local_identity: tuple[str, str, str, str],
    *,
    actor_login: str,
    committer_policy: dict[str, str],
    request: JsonRequest,
) -> dict[str, Any]:
    author, committer, verification, tree_sha = _github_commit_record_fields(
        repository,
        commit_sha,
        local_identity,
        actor_login=actor_login,
        request=request,
        require_verified_signature=False,
    )
    if committer["login"] not in {actor_login, committer_policy["login"]}:
        raise CollectionError("GitHub automation committer is not approved")
    return {
        "record_type": "github-verified-commit",
        "commit_sha": commit_sha,
        "repository": repository,
        "ref": "refs/heads/main",
        "actor_login": actor_login,
        "author": author,
        "committer": committer,
        "verification": verification,
        "tree_sha": tree_sha,
        "main_binding": _main_binding(repository, commit_sha, request=request),
    }


def _pull_request_head_record(
    repository: str,
    ref: str,
    commit_sha: str,
    pull_request_number: int,
    local_identity: tuple[str, str, str, str],
    *,
    actor_login: str,
    request: JsonRequest,
) -> dict[str, Any]:
    author, committer, verification, tree_sha = _github_commit_record_fields(
        repository,
        commit_sha,
        local_identity,
        actor_login=actor_login,
        request=request,
        require_verified_signature=False,
    )
    pull = _mapping(request(f"repos/{repository}/pulls/{pull_request_number}"))
    base = _mapping(pull.get("base"))
    base_repo = _mapping(base.get("repo"))
    head = _mapping(pull.get("head"))
    head_repo = _mapping(head.get("repo"))
    user = _mapping(pull.get("user"))
    if (
        pull.get("number") != pull_request_number
        or pull.get("state") != "open"
        or pull.get("merged") is not False
        or _sha(head.get("sha")) != commit_sha
        or _nonempty(base_repo.get("full_name")) != repository
        or _nonempty(base.get("ref")) != "main"
        or _nonempty(user.get("login")) != actor_login
    ):
        raise CollectionError("GitHub pull request head is not approved")
    return {
        "record_type": "pull-request-head",
        "commit_sha": commit_sha,
        "repository": repository,
        "ref": ref,
        "actor_login": actor_login,
        "author": author,
        "committer": committer,
        "verification": verification,
        "tree_sha": tree_sha,
        "pull_request": {
            "number": pull_request_number,
            "state": "open",
            "merged": False,
            "base_repository": repository,
            "base_ref": "main",
            "head_repository": _nonempty(head_repo.get("full_name")),
            "head_ref": _nonempty(head.get("ref")),
            "head_sha": commit_sha,
            "user_login": actor_login,
        },
    }


def _required_check(
    repository: str,
    head_sha: str,
    *,
    check_policy: dict[str, str],
    request: JsonRequest,
) -> dict[str, str]:
    check_response = _mapping(
        request(f"repos/{repository}/commits/{head_sha}/check-runs?per_page=100")
    )
    check_runs = check_response.get("check_runs")
    total_count = check_response.get("total_count")
    if (
        not isinstance(check_runs, list)
        or type(total_count) is not int
        or total_count != len(check_runs)
    ):
        raise CollectionError("GitHub check-run response is incomplete")
    matching_checks: list[dict[str, Any]] = []
    for item in check_runs:
        check = _mapping(item)
        app = check.get("app")
        app_slug = _mapping(app).get("slug") if isinstance(app, dict) else None
        if check.get("name") == check_policy["name"] and app_slug == check_policy["app_slug"]:
            matching_checks.append(check)
    if len(matching_checks) != 1:
        raise CollectionError("required GitHub check is missing or ambiguous")
    required_check = matching_checks[0]
    if (
        required_check.get("status") != check_policy["status"]
        or required_check.get("conclusion") != check_policy["conclusion"]
    ):
        raise CollectionError("required GitHub check did not succeed")
    return {
        "name": check_policy["name"],
        "app_slug": check_policy["app_slug"],
        "status": check_policy["status"],
        "conclusion": check_policy["conclusion"],
        "head_sha": head_sha,
    }


def _pull_request_record(
    repository: str,
    commit_sha: str,
    merge_tree_sha: str,
    *,
    actor_login: str,
    check_policy: dict[str, str],
    request: JsonRequest,
) -> dict[str, Any]:
    # The associated-PR endpoint is intentionally required to return exactly one item. This
    # rejects ambiguous or additional associations instead of selecting one heuristically.
    associated = request(f"repos/{repository}/commits/{commit_sha}/pulls?per_page=100")
    if not isinstance(associated, list) or len(associated) != 1:
        raise CollectionError("GitHub associated pull request is ambiguous")
    number = _mapping(associated[0]).get("number")
    if type(number) is not int or number < 1:
        raise CollectionError("GitHub pull request number unavailable")
    pull = _mapping(request(f"repos/{repository}/pulls/{number}"))
    base = _mapping(pull.get("base"))
    base_repo = _mapping(base.get("repo"))
    user = _mapping(pull.get("user"))
    head = _mapping(pull.get("head"))
    merged_at = _nonempty(pull.get("merged_at"))
    head_sha = _sha(head.get("sha"))
    merge_commit_sha = _sha(pull.get("merge_commit_sha"))
    base_repository = _nonempty(base_repo.get("full_name"))
    base_ref = _nonempty(base.get("ref"))
    user_login = _nonempty(user.get("login"))
    if (
        merged_at == "null"
        or merge_commit_sha != commit_sha
        or base_repository != repository
        or base_ref != "main"
        or user_login != actor_login
    ):
        raise CollectionError("GitHub pull request provenance is not an approved squash")

    head_commit = _mapping(request(f"repos/{repository}/commits/{head_sha}"))
    head_commit_details = _mapping(head_commit.get("commit"))
    head_tree_sha = _sha(_mapping(head_commit_details.get("tree")).get("sha"))
    if head_tree_sha != merge_tree_sha:
        raise CollectionError("GitHub squash tree does not match pull-request head tree")
    required_check = _required_check(
        repository, head_sha, check_policy=check_policy, request=request
    )
    return {
        "number": number,
        "merged_at": merged_at,
        "merge_commit_sha": merge_commit_sha,
        "base_repository": base_repository,
        "base_ref": base_ref,
        "user_login": user_login,
        "head_sha": head_sha,
        "head_tree_sha": head_tree_sha,
        "merge_tree_sha": merge_tree_sha,
        "required_check_count": 1,
        "required_check": required_check,
    }


def _commit_record(
    repository: str,
    commit_sha: str,
    *,
    committer_policy: dict[str, str],
    check_policy: dict[str, str],
    actor_login: str,
    request: JsonRequest,
) -> dict[str, Any]:
    commit = _mapping(request(f"repos/{repository}/commits/{commit_sha}"))
    commit_author, commit_committer, merge_tree_sha = _commit_identity(
        commit,
        commit_sha,
        actor_login=actor_login,
        committer_policy=committer_policy,
    )
    pull_request = _pull_request_record(
        repository,
        commit_sha,
        merge_tree_sha,
        actor_login=actor_login,
        check_policy=check_policy,
        request=request,
    )
    return {
        "commit_sha": commit_sha,
        "repository": repository,
        "ref": "refs/heads/main",
        "actor_login": actor_login,
        "author": commit_author,
        "committer": commit_committer,
        "verification": {"verified": True, "reason": "valid"},
        "associated_pr_count": 1,
        "main_binding": _main_binding(repository, commit_sha, request=request),
        "pull_request": pull_request,
    }


def _record_for_commit(
    repo: Path,
    commit_sha: str,
    *,
    collection: dict[str, Any],
    request: JsonRequest,
) -> dict[str, Any]:
    repository = collection["repository"]
    actor_login = collection["actor_login"]
    local_identity = _local_identity(repo, commit_sha)
    if collection["ref"].startswith("refs/pull/") and commit_sha == collection["head_sha"]:
        pull_request_number = collection["pull_request_number"]
        if not isinstance(pull_request_number, int) or pull_request_number < 1:
            raise CollectionError("pull request number is required for an unmerged head")
        return _pull_request_head_record(
            repository,
            collection["ref"],
            commit_sha,
            pull_request_number,
            local_identity,
            actor_login=actor_login,
            request=request,
        )
    automation_policy = collection["automation_policy"]
    if local_identity == (
        automation_policy["name"],
        automation_policy["email"],
        automation_policy["name"],
        automation_policy["email"],
    ):
        return _verified_commit_record(
            repository,
            commit_sha,
            local_identity,
            actor_login=actor_login,
            committer_policy=collection["committer_policy"],
            request=request,
        )
    return _commit_record(
        repository,
        commit_sha,
        committer_policy=collection["committer_policy"],
        check_policy=collection["check_policy"],
        actor_login=_actor_login_for_commit(actor_login, collection["actor_overrides"], commit_sha),
        request=request,
    )


def collect(
    repo: Path,
    *,
    repository: str,
    ref: str,
    head_sha: str,
    pull_request_number: int | None = None,
    request: JsonRequest = _gh_json,
) -> dict[str, Any]:
    (
        automation_policy,
        committer_policy,
        check_policy,
        policy_repository,
        policy_ref,
        actor_login,
        actor_overrides,
    ) = _policy(repo)
    if repository != policy_repository or not ref or not head_sha:
        raise CollectionError("repository or ref does not match publication policy")
    local_head = _sha(_run_git(repo, ["rev-parse", "--verify", "HEAD"]).strip())
    if local_head != _sha(head_sha):
        raise CollectionError("requested head does not match checked-out Git HEAD")
    commit_lines = _run_git(repo, ["rev-list", "--topo-order", "HEAD"]).splitlines()
    collection = {
        "repository": repository,
        "ref": ref,
        "head_sha": head_sha,
        "pull_request_number": pull_request_number,
        "automation_policy": automation_policy,
        "committer_policy": committer_policy,
        "check_policy": check_policy,
        "actor_login": actor_login,
        "actor_overrides": actor_overrides,
    }
    records = [
        _record_for_commit(repo, _sha(commit_sha), collection=collection, request=request)
        for commit_sha in commit_lines
    ]
    if policy_ref != "refs/heads/main":
        raise CollectionError("publication policy ref is not protected main")
    context = _actions_context()
    document = {
        "schema_version": 1,
        "repository": repository,
        "ref": ref,
        "head_sha": _sha(head_sha),
        "records": records,
    }
    if context is not None:
        document["context"] = context
    return document


def _write_output(repo: Path, path: Path, document: dict[str, Any]) -> None:
    if not _evidence_path_is_safe(repo, path):
        raise CollectionError("evidence output must be outside checkout")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=".github-provenance-", delete=False
    ) as handle:
        temporary = Path(handle.name)
        json.dump(document, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.chmod(temporary, 0o600)
    temporary.replace(path)
    os.chmod(path, 0o600)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--ref", default=os.environ.get("GITHUB_REF", ""))
    parser.add_argument("--head-sha", default="")
    parser.add_argument(
        "--pull-request",
        type=int,
        default=int(os.environ["PR_NUMBER"]) if os.environ.get("PR_NUMBER") else None,
    )
    args = parser.parse_args(argv)
    repo = Path(__file__).resolve().parents[1]
    try:
        head_sha = args.head_sha or _run_git(repo, ["rev-parse", "--verify", "HEAD"]).strip()
        document = collect(
            repo,
            repository=args.repository,
            ref=args.ref,
            head_sha=head_sha,
            pull_request_number=args.pull_request,
        )
        _write_output(repo, args.output, document)
    except (CollectionError, OSError, ValueError):
        print("github provenance collection failed", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
