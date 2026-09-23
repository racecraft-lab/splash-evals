"""Local privacy audit and allowlisted aggregate export preparation."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast, overload

from .runs import (
    _CORE_EVIDENCE_LIMITATION,
    RunError,
    get_state_dir,
    load_policies,
    load_run,
    project_root,
)
from .sandbox import (
    SandboxError,
    SandboxPolicy,
    SandboxQualification,
    public_qualification_evidence,
    validate_attestation,
)


class PublicationRefused(RunError):
    """The privacy/publication gate did not pass."""


_CONTENT_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("absolute-user-path", re.compile(r"/(?:Users|home)/[^/\s]+/")),
    ("private-key-marker", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    (
        "github-token-shape",
        re.compile(r"\b(?:gh[opurs]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    ),
    ("openai-key-shape", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b")),
    ("aws-access-key-shape", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    (
        "email-address",
        re.compile(
            r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@(?!example\.(?:com|org|net)\b)[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
        ),
    ),
)

_DENIED_EXACT_NAMES = {
    ".env",
    "conversation",
    "raw_response",
    "model_weights",
}

_DENIED_NAME_PARTS = {
    "private_workspace_bootstrap",
    "splash_evaluation_handoff",
}

_GIT_HASH_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
_GIT_IDENTITY_RE = re.compile(r"(?P<name>.+?) <(?P<email>[^<>]+)> -?\d+ [+-]\d{4}")
_GITHUB_PROVENANCE_ENV = "GITHUB_PROVENANCE_EVIDENCE"
_GITHUB_CONTEXT_ENV = {
    "run_id": "GITHUB_RUN_ID",
    "run_attempt": "GITHUB_RUN_ATTEMPT",
    "event_name": "GITHUB_EVENT_NAME",
    "workflow_ref": "GITHUB_WORKFLOW_REF",
}
_SIGNATURE_MARKERS = (
    b"-----BEGIN PGP SIGNATURE-----",
    b"-----BEGIN PGP MESSAGE-----",
    b"-----BEGIN SSH SIGNATURE-----",
    b"-----BEGIN SIGNED MESSAGE-----",
)
_CORE_FAMILY_COUNTS = {
    "gpqa_diamond": 12,
    "ifeval": 16,
    "mmlu_pro": 14,
    "tool_json": 10,
    "context": 8,
}


@overload
def _git(
    repo: Path,
    args: list[str],
    *,
    raw: Literal[False] = False,
) -> subprocess.CompletedProcess[str]: ...


@overload
def _git(
    repo: Path,
    args: list[str],
    *,
    raw: Literal[True],
) -> subprocess.CompletedProcess[bytes]: ...


def _git(
    repo: Path,
    args: list[str],
    *,
    raw: bool = False,
) -> subprocess.CompletedProcess[Any]:
    executable = shutil.which("git")
    if executable is None:
        raise PublicationRefused("git executable is unavailable")
    return subprocess.run(  # noqa: S603 - resolved executable and internal allowlisted arguments.
        [executable, "-C", str(repo), *args],
        check=False,
        capture_output=True,
        text=not raw,
        timeout=30,
    )


def _content_rule_findings(
    text: str,
    policies: dict[str, Any],
    *,
    prefix: str = "",
    additional_allowed_emails: set[str] | frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    publication = policies.get("publication", {})
    if not isinstance(publication, dict):
        publication = {}
    allowed_public_emails = {
        item.casefold()
        for item in publication.get("allowed_public_emails", [])
        if isinstance(item, str)
    }
    allowed_public_emails.update(item.casefold() for item in additional_allowed_emails)
    findings: list[dict[str, Any]] = []
    for rule, pattern in _CONTENT_RULES:
        matches = pattern.findall(text)
        if rule == "email-address":
            matches = [match for match in matches if match.casefold() not in allowed_public_emails]
        if matches:
            findings.append(
                {
                    "rule": f"{prefix}{rule}",
                    "severity": "block",
                    "matched_text_redacted": True,
                    "match_count": len(matches),
                }
            )
    return findings


def _candidate_files(repo: Path) -> list[Path]:
    listing = _git(repo, ["ls-files", "-z", "--cached", "--others", "--exclude-standard"])
    if listing.returncode != 0:
        raise PublicationRefused("unable to resolve tracked and publication-candidate files")
    files: list[Path] = []
    for relative in listing.stdout.split("\0"):
        if not relative:
            continue
        path = repo / relative
        if path.exists() and not path.is_dir():
            files.append(path)
    return sorted(files)


def _scan_file(path: Path, repo: Path, policies: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    relative = path.relative_to(repo).as_posix()
    lower_name = path.name.casefold()
    if path.is_symlink():
        return [{"rule": "symlink-excluded", "path": relative, "severity": "block"}]
    path_parts = {part.casefold() for part in Path(relative).parts}
    if path_parts & _DENIED_EXACT_NAMES or any(part in lower_name for part in _DENIED_NAME_PARTS):
        findings.append({"rule": "denied-filename", "path": relative, "severity": "block"})
    size = path.stat().st_size
    max_size = int(policies["publication"]["max_file_bytes"])
    if relative == "uv.lock":
        max_size = int(policies["publication"].get("max_root_uv_lock_bytes", max_size))
    if size > max_size:
        findings.append({"rule": "file-too-large", "path": relative, "severity": "block"})
        return findings
    suffix = path.suffix.casefold()
    denied = set(policies["publication"]["denied_suffixes"])
    allowed = set(policies["publication"]["allowed_suffixes"])
    if suffix in denied or (suffix and suffix not in allowed):
        findings.append(
            {"rule": "uninspectable-or-denied-type", "path": relative, "severity": "block"}
        )
        return findings
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        findings.append({"rule": "non-utf8-content", "path": relative, "severity": "block"})
        return findings
    if text.startswith("version https://git-lfs.github.com/spec/v1"):
        findings.append({"rule": "git-lfs-pointer", "path": relative, "severity": "block"})
    findings.extend(
        {
            **finding,
            "path": relative,
        }
        for finding in _content_rule_findings(text, policies)
    )
    return findings


def _decode_git_object(raw: bytes) -> tuple[str, str] | None:
    separator = raw.find(b"\n\n")
    if separator < 0:
        return None
    try:
        return (
            raw[:separator].decode("utf-8"),
            raw[separator + 2 :].decode("utf-8"),
        )
    except UnicodeDecodeError:
        return None


def _git_headers(raw_headers: str) -> dict[str, list[str]] | None:
    headers: dict[str, list[str]] = {}
    current: str | None = None
    for line in raw_headers.split("\n"):
        if line.startswith(" "):
            if current is None:
                return None
            headers[current][-1] += f"\n{line[1:]}"
            continue
        key, separator, value = line.partition(" ")
        if not key or not separator:
            return None
        headers.setdefault(key, []).append(value)
        current = key
    return headers


def _first_git_header(headers: dict[str, list[str]], name: str) -> str | None:
    values = headers.get(name)
    return values[0] if values else None


def _parse_git_identity(value: str) -> tuple[str, str] | None:
    match = _GIT_IDENTITY_RE.fullmatch(value)
    if match is None:
        return None
    return match.group("name"), match.group("email")


def _has_signature(raw: bytes) -> bool:
    return any(marker in raw for marker in _SIGNATURE_MARKERS)


def _exact_mapping(value: Any, keys: set[str]) -> dict[str, Any] | None:
    if not isinstance(value, dict) or set(value) != keys:
        return None
    return value


def _nonempty_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _provenance_sha(value: Any) -> str | None:
    if not isinstance(value, str) or _GIT_HASH_RE.fullmatch(value) is None:
        return None
    return value


def _github_context_from_environment() -> dict[str, Any] | None:
    raw = {key: os.environ.get(variable) for key, variable in _GITHUB_CONTEXT_ENV.items()}
    if not any(value is not None for value in raw.values()):
        return None
    if not all(isinstance(value, str) and value for value in raw.values()):
        return {}
    run_id_raw = raw["run_id"]
    run_attempt_raw = raw["run_attempt"]
    if (
        not isinstance(run_id_raw, str)
        or not isinstance(run_attempt_raw, str)
        or not run_id_raw.isdigit()
        or not run_attempt_raw.isdigit()
    ):
        return {}
    run_id = int(run_id_raw)
    run_attempt = int(run_attempt_raw)
    if run_id < 1 or run_attempt < 1:
        return {}
    return {
        "run_id": run_id,
        "run_attempt": run_attempt,
        "event_name": raw["event_name"],
        "workflow_ref": raw["workflow_ref"],
    }


def _github_squash_policy(policies: dict[str, Any]) -> dict[str, Any] | None:
    publication = policies.get("publication")
    if not isinstance(publication, dict):
        return None
    value = publication.get("github_squash_provenance")
    expected_keys = {
        "repository",
        "ref",
        "actor_login",
        "committer",
        "required_check",
    }
    actual_keys = frozenset(value) if isinstance(value, dict) else frozenset()
    allowed_key_sets = {
        frozenset(expected_keys),
        frozenset(expected_keys | {"actor_overrides"}),
    }
    if actual_keys not in allowed_key_sets:
        return None
    return value


def _github_actor_overrides(value: Any) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    overrides: dict[str, str] = {}
    for commit_sha, actor_login in value.items():
        if _provenance_sha(commit_sha) is None or _nonempty_string(actor_login) is None:
            return None
        overrides[commit_sha] = actor_login
    return overrides


def _github_actor_login(
    actor_login: Any,
    actor_overrides: dict[str, str] | None,
    commit_sha: Any,
) -> str | None:
    base_login = _nonempty_string(actor_login)
    sha = _provenance_sha(commit_sha)
    if base_login is None or actor_overrides is None or sha is None:
        return None
    return actor_overrides.get(sha, base_login)


def _automation_identity(policies: dict[str, Any]) -> tuple[str | None, str | None]:
    publication = policies.get("publication")
    configured = publication.get("automation_identity") if isinstance(publication, dict) else None
    if not isinstance(configured, dict):
        configured = {}
    name = _nonempty_string(configured.get("name")) or os.environ.get("PUBLIC_GIT_AUTHOR_NAME")
    email = _nonempty_string(configured.get("email")) or os.environ.get("PUBLIC_GIT_AUTHOR_EMAIL")
    return name, email


def _github_policy_fields(
    policy: dict[str, Any] | None,
) -> tuple[str, dict[str, str], dict[str, str], dict[str, str]] | None:
    if policy is None:
        return None
    committer = _exact_mapping(policy.get("committer"), {"name", "login"})
    check = _exact_mapping(
        policy.get("required_check"), {"name", "app_slug", "status", "conclusion"}
    )
    actor_login = _nonempty_string(policy.get("actor_login"))
    actor_overrides = _github_actor_overrides(policy.get("actor_overrides", {}))
    if actor_login is None or actor_overrides is None or committer is None or check is None:
        return None
    fields: list[dict[str, Any]] = [committer, check]
    if any(any(_nonempty_string(item) is None for item in value.values()) for value in fields):
        return None
    return (
        actor_login,
        actor_overrides,
        {key: str(value) for key, value in committer.items()},
        {key: str(value) for key, value in check.items()},
    )


def _current_git_ref(repo: Path) -> str | None:
    configured = os.environ.get("GITHUB_REF")
    if configured:
        return configured
    result = _git(repo, ["symbolic-ref", "--quiet", "--short", "HEAD"])
    if result.returncode != 0:
        return None
    branch = result.stdout.strip()
    return f"refs/heads/{branch}" if branch else None


def _current_git_head(repo: Path) -> str | None:
    result = _git(repo, ["rev-parse", "--verify", "HEAD"])
    head = result.stdout.strip()
    return head if result.returncode == 0 and _provenance_sha(head) else None


def _provenance_finding(rule: str) -> dict[str, str]:
    return {"rule": rule, "scope": "git-history", "severity": "block"}


def _evidence_path_is_safe(repo: Path, path: Path) -> bool:
    try:
        repo_root = repo.resolve()
        resolved_path = path.resolve(strict=False)
    except (OSError, RuntimeError):
        return False
    absolute_path = path.absolute()
    if any(component.is_symlink() for component in (absolute_path, *absolute_path.parents)):
        return False
    try:
        resolved_path.relative_to(repo_root)
    except ValueError:
        return True
    return False


def _load_github_provenance(repo: Path, path: Path) -> dict[str, Any] | None:
    try:
        if (
            not _evidence_path_is_safe(repo, path)
            or not path.is_file()
            or path.stat().st_size > 1_000_000
        ):
            return None
        raw = path.read_text(encoding="utf-8")
        document = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    top_keys = {"schema_version", "repository", "ref", "head_sha", "records"}
    if not isinstance(document, dict) or set(document) not in (
        top_keys,
        top_keys | {"context"},
    ):
        return None
    return document


def _evidence_context_matches(document: dict[str, Any]) -> bool:
    expected_context = _github_context_from_environment()
    if "context" not in document:
        return expected_context is None
    context = _exact_mapping(
        document.get("context"), {"run_id", "run_attempt", "event_name", "workflow_ref"}
    )
    context_valid = (
        context is not None
        and type(context.get("run_id")) is int
        and context.get("run_id", 0) > 0
        and type(context.get("run_attempt")) is int
        and context.get("run_attempt", 0) > 0
        and _nonempty_string(context.get("event_name")) is not None
        and _nonempty_string(context.get("workflow_ref")) is not None
    )
    return context_valid and expected_context not in (None, {}) and context == expected_context


def _provenance_top_context(
    document: dict[str, Any],
    policies: dict[str, Any],
    *,
    current_ref: str | None,
    current_head: str | None,
) -> (
    tuple[str, str, str, dict[str, str], dict[str, str], dict[str, str]] | None
):
    policy = _github_squash_policy(policies)
    policy_fields = _github_policy_fields(policy)
    if policy is None or policy_fields is None:
        return None
    repository = _nonempty_string(policy.get("repository"))
    policy_ref = _nonempty_string(policy.get("ref"))
    if (
        _nonempty_string(document.get("repository")) != repository
        or _nonempty_string(document.get("ref")) != current_ref
        or _provenance_sha(document.get("head_sha")) != current_head
        or type(document.get("schema_version")) is not int
        or document.get("schema_version") != 1
        or not repository
        or not policy_ref
        or not isinstance(document.get("records"), list)
    ):
        return None
    actor_login, actor_overrides, committer_policy, check_policy = policy_fields
    return repository, policy_ref, actor_login, actor_overrides, committer_policy, check_policy


def _validate_main_binding(binding: dict[str, Any], commit_sha: str) -> bool:
    status = binding.get("status")
    return (
        binding.get("contained") is True
        and status in {"ahead", "identical"}
        and _provenance_sha(binding.get("main_tip_sha")) is not None
        and binding.get("base_sha") == commit_sha
        and binding.get("merge_base_sha") == commit_sha
    )


def _validate_pull_request(
    pull_request: dict[str, Any],
    *,
    commit_sha: str,
    repository: str,
    policy_ref: str,
    actor_login: str,
    check_policy: dict[str, str],
) -> bool:
    number = pull_request.get("number")
    check = _exact_mapping(pull_request.get("required_check"), set(check_policy) | {"head_sha"})
    required_strings = ("merged_at", "base_repository", "base_ref", "user_login")
    required_shas = ("merge_commit_sha", "head_sha", "head_tree_sha", "merge_tree_sha")
    return (
        type(number) is int
        and number >= 1
        and check is not None
        and type(pull_request.get("required_check_count")) is int
        and pull_request.get("required_check_count") == 1
        and all(_nonempty_string(pull_request.get(key)) is not None for key in required_strings)
        and pull_request["merged_at"] != "null"
        and all(_provenance_sha(pull_request.get(key)) is not None for key in required_shas)
        and pull_request["merge_commit_sha"] == commit_sha
        and pull_request["base_repository"] == repository
        and pull_request["base_ref"] == policy_ref.removeprefix("refs/heads/")
        and pull_request["user_login"] == actor_login
        and pull_request["head_tree_sha"] == pull_request["merge_tree_sha"]
        and check.get("name") == check_policy["name"]
        and check.get("app_slug") == check_policy["app_slug"]
        and check.get("status") == check_policy["status"]
        and check.get("conclusion") == check_policy["conclusion"]
        and check.get("head_sha") == pull_request["head_sha"]
    )


def _validate_provenance_record(
    raw_record: Any,
    *,
    repository: str,
    policy_ref: str,
    actor_login: str,
    actor_overrides: dict[str, str],
    committer_policy: dict[str, str],
    check_policy: dict[str, str],
) -> tuple[str, dict[str, Any]] | None:
    record_keys = {
        "commit_sha",
        "repository",
        "ref",
        "actor_login",
        "author",
        "committer",
        "verification",
        "associated_pr_count",
        "pull_request",
        "main_binding",
    }
    record = _exact_mapping(raw_record, record_keys)
    if record is None:
        return None
    commit_sha = _provenance_sha(record.get("commit_sha"))
    expected_actor_login = _github_actor_login(actor_login, actor_overrides, commit_sha)
    author = _exact_mapping(record.get("author"), {"name", "email", "login"})
    committer = _exact_mapping(record.get("committer"), {"name", "email", "login"})
    verification = _exact_mapping(record.get("verification"), {"verified", "reason"})
    pull_request = _exact_mapping(
        record.get("pull_request"),
        {
            "number",
            "merged_at",
            "merge_commit_sha",
            "base_repository",
            "base_ref",
            "user_login",
            "head_sha",
            "head_tree_sha",
            "merge_tree_sha",
            "required_check_count",
            "required_check",
        },
    )
    main_binding = _exact_mapping(
        record.get("main_binding"),
        {"contained", "main_tip_sha", "status", "base_sha", "merge_base_sha"},
    )
    if (
        commit_sha is None
        or expected_actor_login is None
        or _nonempty_string(record.get("repository")) != repository
        or _nonempty_string(record.get("ref")) != policy_ref
        or _nonempty_string(record.get("actor_login")) != expected_actor_login
        or author is None
        or committer is None
        or verification is None
        or pull_request is None
        or main_binding is None
        or type(record.get("associated_pr_count")) is not int
        or record.get("associated_pr_count") != 1
        or not all(_nonempty_string(value) for value in author.values())
        or not all(_nonempty_string(value) for value in committer.values())
        or author.get("login") != expected_actor_login
        or committer.get("name") != committer_policy.get("name")
        or committer.get("login") != committer_policy.get("login")
        or verification.get("verified") is not True
        or verification.get("reason") != "valid"
        or not _validate_main_binding(main_binding, commit_sha)
        or not _validate_pull_request(
            pull_request,
            commit_sha=commit_sha,
            repository=repository,
            policy_ref=policy_ref,
            actor_login=expected_actor_login,
            check_policy=check_policy,
        )
    ):
        return None
    return commit_sha, record


def _validate_github_provenance(
    repo: Path,
    path: Path,
    policies: dict[str, Any],
    *,
    current_ref: str | None,
    current_head: str | None,
) -> tuple[list[dict[str, str]], dict[str, dict[str, Any]]]:
    document = _load_github_provenance(repo, path)
    policy_context = (
        _provenance_top_context(
            document, policies, current_ref=current_ref, current_head=current_head
        )
        if document is not None and _evidence_context_matches(document)
        else None
    )
    if policy_context is None or document is None:
        return [_provenance_finding("github-provenance-invalid")], {}
    (
        repository,
        policy_ref,
        actor_login,
        actor_overrides,
        committer_policy,
        check_policy,
    ) = policy_context
    records: dict[str, dict[str, Any]] = {}
    for raw_record in document["records"]:
        validated = _validate_provenance_record(
            raw_record,
            repository=repository,
            policy_ref=policy_ref,
            actor_login=actor_login,
            actor_overrides=actor_overrides,
            committer_policy=committer_policy,
            check_policy=check_policy,
        )
        if validated is None or validated[0] in records:
            return [_provenance_finding("github-provenance-invalid")], {}
        records[validated[0]] = validated[1]
    return [], records


def _approved_signature(
    repo: Path,
    args: list[str],
    *,
    approved_name: str | None,
    approved_email: str | None,
) -> bool:
    if not approved_name or not approved_email:
        return False
    result = _git(repo, args)
    if result.returncode != 0:
        return False
    expected_identity = f"{approved_name} <{approved_email}>"
    valid_signature = False
    signer_identities: list[str] = []
    for line in (result.stdout + "\n" + result.stderr).splitlines():
        if line.startswith("[GNUPG:] VALIDSIG "):
            valid_signature = True
        elif line.startswith("[GNUPG:] GOODSIG "):
            _, separator, identity = line[len("[GNUPG:] GOODSIG ") :].partition(" ")
            if separator and identity:
                signer_identities.append(identity.strip())
    return valid_signature and expected_identity in signer_identities


def _scoped_content_findings(
    text: str,
    policies: dict[str, Any],
    *,
    prefix: str,
    scope: str,
    approved_email: str | None,
    additional_allowed_emails: set[str] | frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    additional_emails = set(additional_allowed_emails)
    if approved_email:
        additional_emails.add(approved_email)
    return [
        {
            **finding,
            "scope": scope,
        }
        for finding in _content_rule_findings(
            text,
            policies,
            prefix=prefix,
            additional_allowed_emails=additional_emails,
        )
    ]


def _commit_findings(
    repo: Path,
    commit_hash: str,
    policies: dict[str, Any],
    *,
    approved_name: str | None,
    approved_email: str | None,
    github_record: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], bool, bool]:
    findings: list[dict[str, Any]] = []
    raw_result = _git(repo, ["cat-file", "commit", commit_hash], raw=True)
    if raw_result.returncode != 0:
        return (
            [
                {
                    "rule": "uninspectable-commit-object",
                    "scope": "git-history",
                    "severity": "block",
                }
            ],
            False,
            False,
        )
    decoded = _decode_git_object(raw_result.stdout)
    if decoded is None:
        return (
            [
                {
                    "rule": "uninspectable-commit-content",
                    "scope": "git-history",
                    "severity": "block",
                }
            ],
            False,
            False,
        )
    header_text, body_text = decoded
    headers = _git_headers(header_text)
    if headers is None:
        return (
            [
                {
                    "rule": "uninspectable-commit-metadata",
                    "scope": "git-history",
                    "severity": "block",
                }
            ],
            False,
            False,
        )
    author = _parse_git_identity(_first_git_header(headers, "author") or "")
    committer = _parse_git_identity(_first_git_header(headers, "committer") or "")
    if author is None or committer is None:
        findings.append(
            {
                "rule": "uninspectable-commit-metadata",
                "scope": "git-history",
                "severity": "block",
            }
        )
    tree_sha = _first_git_header(headers, "tree")
    platform_valid = False
    if github_record is not None and author is not None and committer is not None:
        record_author = github_record.get("author")
        record_committer = github_record.get("committer")
        pull_request = github_record.get("pull_request")
        platform_valid = (
            isinstance(record_author, dict)
            and isinstance(record_committer, dict)
            and isinstance(pull_request, dict)
            and author == (record_author.get("name"), record_author.get("email"))
            and committer == (record_committer.get("name"), record_committer.get("email"))
            and tree_sha == pull_request.get("merge_tree_sha")
        )
    automation_valid = bool(
        approved_name
        and approved_email
        and author == (approved_name, approved_email)
        and committer == (approved_name, approved_email)
    )
    if author is not None and committer is not None and not automation_valid and not platform_valid:
        findings.append(
            {
                "rule": "history-identity-mismatch",
                "scope": "git-history",
                "severity": "block",
            }
        )
        findings.append(
            _provenance_finding(
                "github-squash-provenance-invalid"
                if github_record is not None
                else "github-squash-provenance-required"
            )
        )
    object_text = f"{header_text}\n\n{body_text}"
    github_allowed_emails: set[str] = set()
    if github_record is not None:
        for identity_key in ("author", "committer"):
            identity = github_record.get(identity_key)
            if isinstance(identity, dict) and isinstance(identity.get("email"), str):
                github_allowed_emails.add(identity["email"])
    findings.extend(
        _scoped_content_findings(
            object_text,
            policies,
            prefix="commit-message-",
            scope="git-history",
            approved_email=approved_email,
            additional_allowed_emails=github_allowed_emails,
        )
    )
    signed = "gpgsig" in headers
    if (
        signed
        and not platform_valid
        and not _approved_signature(
            repo,
            ["verify-commit", "--raw", commit_hash],
            approved_name=approved_name,
            approved_email=approved_email,
        )
    ):
        findings.append(
            {
                "rule": "signed-commit-requires-manual-review",
                "scope": "git-history",
                "severity": "block",
            }
        )
    if "mergetag" in headers:
        findings.append(
            {
                "rule": "signed-mergetag-requires-manual-review",
                "scope": "git-history",
                "severity": "block",
            }
        )
    return findings, True, signed


def _tag_findings(
    repo: Path,
    ref: str,
    policies: dict[str, Any],
    *,
    approved_name: str | None,
    approved_email: str | None,
) -> tuple[list[dict[str, Any]], str | None, str | None, bool, bool, bool]:
    findings: list[dict[str, Any]] = []
    if not ref.startswith("refs/tags/"):
        return (
            [{"rule": "uninspectable-tag-metadata", "scope": "git-tags", "severity": "block"}],
            None,
            None,
            False,
            False,
            False,
        )
    findings.extend(
        _scoped_content_findings(
            ref,
            policies,
            prefix="tag-ref-",
            scope="git-tags",
            approved_email=approved_email,
        )
    )
    object_type_result = _git(repo, ["cat-file", "-t", ref])
    if object_type_result.returncode != 0:
        findings.append(
            {"rule": "uninspectable-tag-object", "scope": "git-tags", "severity": "block"}
        )
        return findings, None, None, False, False, False
    object_type = object_type_result.stdout.strip()
    if object_type != "tag":
        if object_type not in {"commit", "tree", "blob"}:
            findings.append(
                {"rule": "uninspectable-tag-object", "scope": "git-tags", "severity": "block"}
            )
            return findings, object_type or None, None, False, False, False
        findings.append(
            {
                "rule": "lightweight-tag-requires-manual-review",
                "scope": "git-tags",
                "severity": "block",
            }
        )
        return findings, object_type, None, False, True, False

    object_id_result = _git(repo, ["rev-parse", "--verify", ref])
    tag_object_id = object_id_result.stdout.strip()
    if object_id_result.returncode != 0 or _GIT_HASH_RE.fullmatch(tag_object_id) is None:
        findings.append(
            {"rule": "uninspectable-tag-object", "scope": "git-tags", "severity": "block"}
        )
        return findings, object_type, None, True, False, False
    raw_result = _git(repo, ["cat-file", "tag", tag_object_id], raw=True)
    decoded = _decode_git_object(raw_result.stdout) if raw_result.returncode == 0 else None
    if decoded is None:
        findings.append(
            {"rule": "uninspectable-tag-content", "scope": "git-tags", "severity": "block"}
        )
        return findings, object_type, None, True, False, False
    header_text, body_text = decoded
    headers = _git_headers(header_text)
    if headers is None:
        findings.append(
            {"rule": "uninspectable-tag-metadata", "scope": "git-tags", "severity": "block"}
        )
        return findings, object_type, None, True, False, False
    tagger = _parse_git_identity(_first_git_header(headers, "tagger") or "")
    if tagger is None:
        findings.append(
            {"rule": "uninspectable-tag-metadata", "scope": "git-tags", "severity": "block"}
        )
    elif not approved_name or not approved_email or tagger != (approved_name, approved_email):
        findings.append({"rule": "tag-identity-mismatch", "scope": "git-tags", "severity": "block"})
    expected_tag_name = ref.removeprefix("refs/tags/")
    if _first_git_header(headers, "tag") != expected_tag_name:
        findings.append(
            {
                "rule": "tag-metadata-name-mismatch",
                "scope": "git-tags",
                "severity": "block",
            }
        )
    target_object = _first_git_header(headers, "object")
    declared_target_type = _first_git_header(headers, "type")
    actual_target_type: str | None = None
    if (
        target_object is None
        or declared_target_type is None
        or _GIT_HASH_RE.fullmatch(target_object) is None
    ):
        findings.append(
            {"rule": "uninspectable-tag-object", "scope": "git-tags", "severity": "block"}
        )
    else:
        target_type_result = _git(repo, ["cat-file", "-t", target_object])
        actual_target_type = target_type_result.stdout.strip() or None
        if target_type_result.returncode != 0 or actual_target_type is None:
            findings.append(
                {"rule": "uninspectable-tag-object", "scope": "git-tags", "severity": "block"}
            )
        elif actual_target_type != declared_target_type:
            findings.append(
                {"rule": "tag-object-type-mismatch", "scope": "git-tags", "severity": "block"}
            )
    object_text = f"{header_text}\n\n{body_text}"
    findings.extend(
        _scoped_content_findings(
            object_text,
            policies,
            prefix="tag-content-",
            scope="git-tags",
            approved_email=approved_email,
        )
    )
    signed = _has_signature(raw_result.stdout)
    if signed and not _approved_signature(
        repo,
        ["verify-tag", "--raw", ref],
        approved_name=approved_name,
        approved_email=approved_email,
    ):
        findings.append(
            {
                "rule": "signed-tag-requires-manual-review",
                "scope": "git-tags",
                "severity": "block",
            }
        )
    return findings, object_type, actual_target_type, True, False, signed


def _identity_configuration(
    repo: Path,
    policies: dict[str, Any],
) -> tuple[list[dict[str, Any]], str | None, str | None, str | None, str | None]:
    findings: list[dict[str, Any]] = []
    name_result = _git(repo, ["config", "--local", "--get", "user.name"])
    email_result = _git(repo, ["config", "--local", "--get", "user.email"])
    configured_name = name_result.stdout.strip() if name_result.returncode == 0 else None
    configured_email = email_result.stdout.strip() if email_result.returncode == 0 else None
    env_name = os.environ.get("PUBLIC_GIT_AUTHOR_NAME")
    env_email = os.environ.get("PUBLIC_GIT_AUTHOR_EMAIL")
    approved_name, approved_email = _automation_identity(policies)
    publication = policies.get("publication")
    automation_policy = (
        publication.get("automation_identity") if isinstance(publication, dict) else None
    )
    if not env_name or not env_email:
        findings.append(
            {
                "rule": "approved-public-identity-unresolved",
                "scope": "git-identity",
                "severity": "block",
            }
        )
    elif configured_name != approved_name or configured_email != approved_email:
        findings.append(
            {"rule": "repo-local-identity-mismatch", "scope": "git-identity", "severity": "block"}
        )
    if isinstance(automation_policy, dict) and (
        env_name != _nonempty_string(automation_policy.get("name"))
        or env_email != _nonempty_string(automation_policy.get("email"))
    ):
        findings.append(
            {
                "rule": "approved-public-identity-policy-mismatch",
                "scope": "git-identity",
                "severity": "block",
            }
        )
    return findings, configured_name, configured_email, approved_name, approved_email


def _provenance_context(
    repo: Path,
    policies: dict[str, Any],
    path: Path | None,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    if path is None:
        return [], {}
    findings, records = _validate_github_provenance(
        repo,
        path,
        policies,
        current_ref=_current_git_ref(repo),
        current_head=_current_git_head(repo),
    )
    return findings, records


def _history_findings(
    repo: Path,
    policies: dict[str, Any],
    *,
    approved_name: str | None,
    approved_email: str | None,
    provenance_records: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], int, int]:
    findings: list[dict[str, Any]] = []
    history = _git(repo, ["rev-list", "--topo-order", "HEAD"])
    commits_checked = 0
    signed_commits_checked = 0
    if history.returncode != 0:
        return (
            [{"rule": "uninspectable-git-history", "scope": "git-history", "severity": "block"}],
            commits_checked,
            signed_commits_checked,
        )
    for commit_hash in history.stdout.splitlines():
        if _GIT_HASH_RE.fullmatch(commit_hash) is None:
            findings.append(
                {
                    "rule": "uninspectable-commit-metadata",
                    "scope": "git-history",
                    "severity": "block",
                }
            )
            continue
        commit_findings, inspected, signed = _commit_findings(
            repo,
            commit_hash,
            policies,
            approved_name=approved_name,
            approved_email=approved_email,
            github_record=provenance_records.get(commit_hash),
        )
        findings.extend(commit_findings)
        commits_checked += int(inspected)
        signed_commits_checked += int(signed)
    return findings, commits_checked, signed_commits_checked


def _notes_findings(repo: Path) -> tuple[list[dict[str, Any]], bool]:
    notes = _git(repo, ["notes", "list"])
    if notes.returncode != 0:
        return [
            {"rule": "uninspectable-git-notes", "scope": "git-history", "severity": "block"}
        ], False
    if notes.stdout.strip():
        return [
            {
                "rule": "git-notes-require-manual-review",
                "scope": "git-history",
                "severity": "block",
            }
        ], True
    return [], False


def _tag_audit(
    repo: Path,
    policies: dict[str, Any],
    *,
    approved_name: str | None,
    approved_email: str | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    tags = _git(repo, ["for-each-ref", "refs/tags", "--format=%(refname)"])
    counts: dict[str, Any] = {
        "tags_checked": 0,
        "annotated_tags_checked": 0,
        "lightweight_tags_checked": 0,
        "signed_tags_checked": 0,
        "tag_object_types": {},
        "tag_target_types": {},
    }
    if tags.returncode != 0:
        return [
            {"rule": "uninspectable-tag-metadata", "scope": "git-tags", "severity": "block"}
        ], counts
    for ref in tags.stdout.splitlines():
        if not ref:
            continue
        counts["tags_checked"] += 1
        tag_findings, object_type, target_type, annotated, lightweight, signed = _tag_findings(
            repo,
            ref,
            policies,
            approved_name=approved_name,
            approved_email=approved_email,
        )
        findings.extend(tag_findings)
        if object_type is not None:
            tag_object_types = counts["tag_object_types"]
            tag_object_types[object_type] = tag_object_types.get(object_type, 0) + 1
        if target_type is not None:
            tag_target_types = counts["tag_target_types"]
            tag_target_types[target_type] = tag_target_types.get(target_type, 0) + 1
        counts["annotated_tags_checked"] += int(annotated)
        counts["lightweight_tags_checked"] += int(lightweight)
        counts["signed_tags_checked"] += int(signed)
    return findings, counts


def _identity_findings(
    repo: Path,
    policies: dict[str, Any] | None = None,
    *,
    provenance_path: Path | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    active_policies = policies or {}
    findings, configured_name, configured_email, approved_name, approved_email = (
        _identity_configuration(repo, active_policies)
    )
    provenance_findings, provenance_records = _provenance_context(
        repo, active_policies, provenance_path
    )
    findings.extend(provenance_findings)
    history_findings, commits_checked, signed_commits_checked = _history_findings(
        repo,
        active_policies,
        approved_name=approved_name,
        approved_email=approved_email,
        provenance_records=provenance_records,
    )
    findings.extend(history_findings)
    note_findings, notes_present = _notes_findings(repo)
    findings.extend(note_findings)
    tag_findings, tag_summary = _tag_audit(
        repo,
        active_policies,
        approved_name=approved_name,
        approved_email=approved_email,
    )
    findings.extend(tag_findings)
    return findings, {
        "repo_local_identity_configured": bool(configured_name and configured_email),
        "approved_identity_environment_present": bool(
            os.environ.get("PUBLIC_GIT_AUTHOR_NAME") and os.environ.get("PUBLIC_GIT_AUTHOR_EMAIL")
        ),
        "github_provenance_supplied": provenance_path is not None,
        "github_provenance_records": len(provenance_records),
        "commits_checked": commits_checked,
        "signed_commits_checked": signed_commits_checked,
        **tag_summary,
        "notes_present": notes_present,
    }


def _run_secret_scanner(repo: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    executable = shutil.which("gitleaks")
    if not executable:
        return (
            [{"rule": "pinned-secret-scanner-unavailable", "scope": "source", "severity": "block"}],
            {"tool": "gitleaks", "available": False, "version": None, "scope": "working-tree"},
        )
    version = subprocess.run(  # noqa: S603 - executable resolved by shutil.which.
        [executable, "version"], check=False, capture_output=True, text=True, timeout=10
    )
    scans = {
        "working-tree": [executable, "dir", str(repo), "--redact", "--no-banner"],
        "git-history": [executable, "git", str(repo), "--redact", "--no-banner"],
    }
    findings = []
    exit_codes: dict[str, int] = {}
    for scope, command in scans.items():
        scan = subprocess.run(  # noqa: S603 - executable resolved by shutil.which.
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
        exit_codes[scope] = scan.returncode
        if scan.returncode != 0:
            findings.append(
                {
                    "rule": "secret-scanner-findings-or-error",
                    "scope": scope,
                    "severity": "block",
                    "diagnostics_redacted": True,
                }
            )
    return findings, {
        "tool": "gitleaks",
        "available": True,
        "version": version.stdout.strip() or version.stderr.strip(),
        "scope": "working-tree-and-git-history",
        "exit_codes": exit_codes,
    }


def _audit_scopes(
    repo: Path,
    policies: dict[str, Any],
    provenance_path: Path | None,
) -> tuple[list[dict[str, Any]], int, dict[str, Any], dict[str, Any]]:
    files = _candidate_files(repo)
    findings = [finding for path in files for finding in _scan_file(path, repo, policies)]
    identity_findings, identity_summary = _identity_findings(
        repo, policies, provenance_path=provenance_path
    )
    findings.extend(identity_findings)
    scanner_findings, scanner = _run_secret_scanner(repo)
    findings.extend(scanner_findings)
    return findings, len(files), identity_summary, scanner


def audit_publication(
    *,
    scope: str = "publication",
    root: Path | None = None,
    provenance_path: Path | None = None,
) -> dict[str, Any]:
    """Audit public source and local Git metadata with redacted diagnostics.

    ``GITHUB_PROVENANCE_EVIDENCE`` is accepted only as an explicit CI path input;
    the file is validated locally and no network access occurs here.
    """
    if scope != "publication":
        raise PublicationRefused("only publication scope is implemented")
    repo = (root or project_root()).resolve()
    if provenance_path is None:
        configured_path = os.environ.get(_GITHUB_PROVENANCE_ENV)
        if configured_path:
            provenance_path = Path(configured_path)
    policies = load_policies(repo)
    findings, files_checked, identity_summary, scanner = _audit_scopes(
        repo, policies, provenance_path
    )
    status = "pass" if not findings else "blocked"
    result = {
        "schema_version": 1,
        "scope": scope,
        "status": status,
        "files_checked": files_checked,
        "findings": findings,
        "identity": identity_summary,
        "secret_scanner": scanner,
        "scopes_checked": [
            "working-tree",
            "git-history",
            "git-identities",
            "git-tags",
            "git-notes",
            "filenames",
            "symlinks",
            "file-types",
            "file-sizes",
        ],
        "limitations": [
            "A clean tested scope is not proof that every possible form of personal "
            "information is absent.",
            "GitHub account activity and third-party contributions may still carry identity "
            "metadata.",
            "Human review of the exact export and outgoing refs remains required before "
            "publication.",
        ],
    }
    state = get_state_dir(repo)
    audit_dir = state / "publication_audits"
    audit_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    audit_path = audit_dir / "latest.json"
    audit_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(audit_path, 0o600)
    return result


_PUBLICATION_RUN_LABEL = "local-pilot"
_CAPABILITY_PUBLICATION = "held_out_model_capability"
_QUALIFICATION_PUBLICATION = "post_hoc_runtime_scorer_qualification"
_POST_HOC_SELECTION = "post_hoc_exploratory"
_UNRESOLVED_EVIDENCE = frozenset({"", "n/a", "none", "unknown", "unresolved"})
_QUALIFICATION_AGGREGATE_FIELDS = frozenset(
    {
        "planned",
        "attempted",
        "completed",
        "scorable",
        "failed",
        "censored",
        "unattempted",
        "end_to_end_deployment_success",
        "failure_types",
    }
)


def _evidence_mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return cast(dict[str, Any], value)


def _qualified_evidence_text(value: Any) -> bool:
    return isinstance(value, str) and value.strip().casefold() not in _UNRESOLVED_EVIDENCE


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _is_exact_int(value: Any, expected: Any) -> bool:
    return type(value) is int and type(expected) is int and value == expected


def _validated_coding_sandbox_evidence(
    manifest: dict[str, Any],
) -> tuple[dict[str, Any] | None, list[str]]:
    """Validate private proof and return only the shared sanitized public schema."""
    policy_evidence = _evidence_mapping(manifest.get("sandbox_policy"))
    attestation_evidence = _evidence_mapping(manifest.get("sandbox_attestation"))
    public_evidence = _evidence_mapping(manifest.get("sandbox_qualification"))
    binding = _evidence_mapping(manifest.get("coding_run_binding"))
    failures: list[str] = []

    try:
        policy = SandboxPolicy(**policy_evidence)
    except (SandboxError, TypeError):
        failures.append("sandbox_policy")
        return None, failures
    try:
        attestation = validate_attestation(attestation_evidence, policy)
    except SandboxError:
        failures.append("sandbox_attestation")
        return None, failures

    expected_public = public_qualification_evidence(
        SandboxQualification(attestation=attestation, private_logs=())
    )
    if set(public_evidence) != set(expected_public):
        failures.append("sandbox_qualification.schema")
    for field in expected_public.keys() - {"qualification_event_count"}:
        if public_evidence.get(field) != expected_public[field]:
            failures.append(f"sandbox_qualification.{field}")
    event_count = public_evidence.get("qualification_event_count")
    if not isinstance(event_count, int) or isinstance(event_count, bool) or event_count <= 0:
        failures.append("sandbox_qualification.qualification_event_count")

    binding_keys = {
        "attestation_file_sha256",
        "attestation_revision",
        "denominator_count",
        "denominator_policy",
        "frozen_before_execution",
        "image_digest",
        "planned_case_count",
        "attempted_case_count",
        "policy_sha256",
        "run_fingerprint_sha256",
    }
    if set(binding) != binding_keys:
        failures.append("coding_run_binding.schema")
    fingerprint = manifest.get("fingerprint")
    attestation_file_sha256 = manifest.get("sandbox_attestation_sha256")
    binding_checks: list[tuple[str, bool]] = [
        ("frozen_before_execution", binding.get("frozen_before_execution") is True),
        ("image_digest", binding.get("image_digest") == attestation.image_digest),
        ("policy_sha256", binding.get("policy_sha256") == attestation.policy_sha256),
        (
            "attestation_revision",
            binding.get("attestation_revision") == attestation.attestation_revision,
        ),
        (
            "attestation_file_sha256",
            _is_sha256(attestation_file_sha256)
            and binding.get("attestation_file_sha256") == attestation_file_sha256,
        ),
        (
            "run_fingerprint_sha256",
            _is_sha256(fingerprint) and binding.get("run_fingerprint_sha256") == fingerprint,
        ),
        ("denominator_policy", binding.get("denominator_policy") == "all_attempted_cases"),
    ]
    planned = binding.get("planned_case_count")
    attempted = binding.get("attempted_case_count")
    denominator = binding.get("denominator_count")
    aggregate = _evidence_mapping(manifest.get("aggregate"))
    valid_case_count = (
        isinstance(planned, int)
        and not isinstance(planned, bool)
        and planned > 0
        and attempted == planned
        and denominator == attempted
        and type(aggregate.get("planned")) is int
        and planned <= aggregate["planned"]
        and type(aggregate.get("attempted")) is int
        and attempted <= aggregate["attempted"]
    )
    binding_checks.append(("case_denominator", valid_case_count))
    failures.extend(f"coding_run_binding.{field}" for field, valid in binding_checks if not valid)
    if failures:
        return None, failures
    return {
        "qualification": public_evidence,
        "attestation_file_sha256": binding["attestation_file_sha256"],
        "run_fingerprint_sha256": binding["run_fingerprint_sha256"],
        "frozen_before_execution": binding["frozen_before_execution"],
        "planned_case_count": planned,
        "attempted_case_count": attempted,
        "denominator_count": denominator,
        "denominator_policy": binding["denominator_policy"],
    }, []


def _valid_core_family_results(values: Any, families: list[Any], aggregate: dict[str, Any]) -> bool:
    if not isinstance(values, list) or len(values) != len(_CORE_FAMILY_COUNTS):
        return False
    total_correct = 0
    for value, (family, count) in zip(values, _CORE_FAMILY_COUNTS.items(), strict=True):
        if not isinstance(value, dict):
            return False
        correct = value.get("correct")
        failures = value.get("failure_counts")
        if not (
            value.get("family") == family
            and _is_exact_int(value.get("planned"), count)
            and _is_exact_int(value.get("attempted"), count)
            and _is_exact_int(value.get("scored"), count)
            and type(correct) is int
            and 0 <= correct <= count
            and isinstance(failures, dict)
            and _is_exact_int(failures.get("incorrect"), count - correct)
            and _is_exact_int(failures.get("execution_error"), 0)
            and _is_exact_int(failures.get("unscored"), 0)
        ):
            return False
        total_correct += correct
    return (
        families == list(_CORE_FAMILY_COUNTS)
        and _is_exact_int(aggregate.get("correct"), total_correct)
        and _is_exact_int(
            aggregate.get("incorrect"), sum(_CORE_FAMILY_COUNTS.values()) - total_correct
        )
    )


def _capability_transport_requirements(suite: Any) -> tuple[str, str]:
    if suite == "core":
        return "evalscope_openai_api", "/v1/chat/completions"
    return "lmstudio_native_v1", "/api/v1/chat"


def _capability_served_checks(
    suite: Any, served: dict[str, Any], model: dict[str, Any]
) -> tuple[bool, bool, bool, bool]:
    requested = served.get("requested_instance_id_sha256")
    response = served.get("response_instance_id_sha256")
    requested_valid = _is_sha256(requested) and requested == model.get("instance_id_sha256")
    if suite == "core":
        return (
            served.get("status") == "verified_request_binding_without_response_identity",
            served.get("match") is None,
            requested_valid,
            response is None,
        )
    return (
        served.get("status") == "verified",
        served.get("match") is True,
        requested_valid,
        _is_sha256(response) and response == requested,
    )


def _capability_reasoning_status(suite: Any) -> str:
    return "transmitted_not_read_back" if suite == "core" else "accepted_by_runtime"


def _valid_core_limitations(suite: Any, limitations: Any) -> bool:
    if suite != "core":
        return True
    return isinstance(limitations, list) and _CORE_EVIDENCE_LIMITATION in limitations


def _valid_suite_family_results(
    suite: Any, values: Any, families: list[Any], aggregate: dict[str, Any]
) -> bool:
    if suite != "core":
        return True
    return _valid_core_family_results(values, families, aggregate)


def _capability_publication_failures(manifest: dict[str, Any]) -> list[str]:
    """Return every missing fact that prevents a capability-evidence export."""
    aggregate = _evidence_mapping(manifest.get("aggregate"))
    protocol = _evidence_mapping(manifest.get("protocol"))
    historical = _evidence_mapping(manifest.get("historical_protocol"))
    selection = _evidence_mapping(manifest.get("selection_evidence"))
    locality = _evidence_mapping(manifest.get("locality_evidence"))
    model = _evidence_mapping(manifest.get("model_instance_evidence"))
    native_model = _evidence_mapping(model.get("native_identity"))
    served = _evidence_mapping(manifest.get("served_model_evidence"))
    runtime = _evidence_mapping(manifest.get("runtime_evidence"))
    transport = _evidence_mapping(manifest.get("transport"))
    reasoning = _evidence_mapping(manifest.get("reasoning_evidence"))
    scorer = _evidence_mapping(manifest.get("scorer_evidence"))
    calibration = _evidence_mapping(scorer.get("calibration"))

    sample_count = historical.get("sample_count")
    valid_sample_count = (
        isinstance(sample_count, int) and not isinstance(sample_count, bool) and sample_count > 0
    )
    sample_manifest = historical.get("sample_id_manifest")
    scorer_id = scorer.get("scorer_id")
    task_families = manifest.get("task_families")
    families = task_families if isinstance(task_families, list) else []
    valid_task_families = bool(families) and all(
        _qualified_evidence_text(item) for item in families
    )
    includes_coding = valid_task_families and any(
        str(item).casefold() == "coding" for item in families
    )
    suite = manifest.get("suite")
    expected_runtime = _capability_transport_requirements(suite)
    served_checks = _capability_served_checks(suite, served, model)
    effective_status = _capability_reasoning_status(suite)
    core_results = manifest.get("family_results")
    valid_core_results = _valid_suite_family_results(suite, core_results, families, aggregate)

    checks: list[tuple[str, bool]] = [
        ("status", manifest.get("status") == "completed"),
        ("suite", suite in {"pilot", "core"}),
        ("evidence_class", manifest.get("evidence_class") == "local_measurement"),
        ("model_is_splash", manifest.get("model_is_splash") is True),
        ("held_out", manifest.get("held_out") is True),
        ("selection_status", manifest.get("selection_status") == "held_out_verified"),
        (
            "calibration_heldout_separation",
            manifest.get("calibration_heldout_separation") != _POST_HOC_SELECTION,
        ),
        ("task_families", valid_task_families),
        ("family_results", valid_core_results),
        ("limitations", _valid_core_limitations(suite, manifest.get("limitations"))),
        ("historical_protocol.sample_count", valid_sample_count),
        (
            "aggregate.planned",
            valid_sample_count and _is_exact_int(aggregate.get("planned"), sample_count),
        ),
        (
            "aggregate.attempted",
            valid_sample_count and _is_exact_int(aggregate.get("attempted"), sample_count),
        ),
        (
            "aggregate.completed",
            valid_sample_count and _is_exact_int(aggregate.get("completed"), sample_count),
        ),
        (
            "aggregate.scorable",
            valid_sample_count and _is_exact_int(aggregate.get("scorable"), sample_count),
        ),
        ("aggregate.failed", _is_exact_int(aggregate.get("failed"), 0)),
        ("aggregate.censored", _is_exact_int(aggregate.get("censored"), 0)),
        ("aggregate.unattempted", _is_exact_int(aggregate.get("unattempted"), 0)),
        ("locality_evidence.status", locality.get("status") == "verified_local"),
        ("locality_evidence.endpoint_loopback", locality.get("endpoint_loopback") is True),
        (
            "locality_evidence.local_instance_evidence",
            locality.get("local_instance_evidence") is True,
        ),
        ("model_instance_evidence.selection", model.get("selection") == "exact_loaded_record"),
        (
            "model_instance_evidence.splash_attribution",
            model.get("splash_attribution") == "confirmed",
        ),
        ("model_instance_evidence.instance_id_sha256", _is_sha256(model.get("instance_id_sha256"))),
        (
            "model_instance_evidence.native_identity.loaded_instance_id_match",
            native_model.get("loaded_instance_id_match") is True,
        ),
        ("served_model_evidence.status", served_checks[0]),
        ("served_model_evidence.match", served_checks[1]),
        (
            "served_model_evidence.requested_instance_id_sha256",
            served_checks[2],
        ),
        (
            "served_model_evidence.response_instance_id_sha256",
            served_checks[3],
        ),
        ("runtime_evidence.transport", runtime.get("transport") == expected_runtime[0]),
        ("runtime_evidence.endpoint", runtime.get("endpoint") == expected_runtime[1]),
        ("runtime_evidence.cli_version", _qualified_evidence_text(runtime.get("cli_version"))),
        ("runtime_evidence.engine", _qualified_evidence_text(runtime.get("engine"))),
        (
            "runtime_evidence.engine_version",
            _qualified_evidence_text(runtime.get("engine_version")),
        ),
        ("transport.redirects", transport.get("redirects") is False),
        ("transport.cloud_fallback", transport.get("cloud_fallback") is False),
        (
            "reasoning_evidence.requested",
            _qualified_evidence_text(reasoning.get("requested")),
        ),
        (
            "reasoning_evidence.transmitted",
            reasoning.get("transmitted") == reasoning.get("requested"),
        ),
        (
            "reasoning_evidence.effective_status",
            reasoning.get("effective_status") == effective_status,
        ),
        (
            "effective_settings_status",
            suite != "core" or manifest.get("effective_settings_status") == effective_status,
        ),
        (
            "selection_evidence.ordered_sample_manifest_sha256",
            _is_sha256(selection.get("ordered_sample_manifest_sha256"))
            and selection.get("ordered_sample_manifest_sha256") == sample_manifest,
        ),
        ("selection_evidence.manifest_source", selection.get("manifest_source") == "external"),
        ("selection_evidence.frozen_before_tuning", selection.get("frozen_before_tuning") is True),
        (
            "selection_evidence.contamination_review_revision",
            _qualified_evidence_text(selection.get("contamination_review_revision")),
        ),
        (
            "historical_protocol.benchmark_version",
            _qualified_evidence_text(historical.get("benchmark_version")),
        ),
        (
            "historical_protocol.dataset_revision",
            _qualified_evidence_text(historical.get("dataset_revision")),
        ),
        ("historical_protocol.split", _qualified_evidence_text(historical.get("split"))),
        ("historical_protocol.sample_id_manifest", _is_sha256(sample_manifest)),
        (
            "historical_protocol.prompts_or_template_revision",
            _qualified_evidence_text(historical.get("prompts_or_template_revision")),
        ),
        ("historical_protocol.attempts_per_task", historical.get("attempts_per_task") == 1),
        (
            "historical_protocol.failure_policy",
            historical.get("failure_policy") == "count_failures_as_incorrect",
        ),
        ("historical_protocol.denominator", historical.get("denominator") == "all_planned_samples"),
        ("scorer_evidence.scorer_id", _qualified_evidence_text(scorer_id)),
        (
            "scorer_evidence.protocol_binding",
            scorer_id == protocol.get("scorer_version") == historical.get("scorer_revision"),
        ),
        ("scorer_evidence.content_sha256", _is_sha256(scorer.get("content_sha256"))),
        ("scorer_evidence.namespace", scorer.get("namespace") == "benchmark"),
        ("scorer_evidence.evidence_class", scorer.get("evidence_class") == "scorer_qualification"),
        (
            "scorer_evidence.eligible_for_capability_report",
            scorer.get("eligible_for_capability_report") is True,
        ),
        ("scorer_evidence.calibration.status", calibration.get("status") == "qualified"),
        (
            "scorer_evidence.calibration.manifest_sha256",
            _is_sha256(calibration.get("manifest_sha256")),
        ),
        (
            "scorer_evidence.calibration.revision",
            _qualified_evidence_text(calibration.get("revision")),
        ),
        (
            "scorer_evidence.calibration.independent_from_evaluation",
            calibration.get("independent_from_evaluation") is True,
        ),
    ]
    if includes_coding:
        _, sandbox_failures = _validated_coding_sandbox_evidence(manifest)
    else:
        sandbox_failures = []
    return [field for field, valid in checks if not valid] + sandbox_failures


def _capability_publication_blockers(manifest: dict[str, Any]) -> list[dict[str, str]]:
    is_candidate = (
        manifest.get("suite") in {"pilot", "core"}
        and manifest.get("evidence_class") == "local_measurement"
        and manifest.get("model_is_splash") is True
        and manifest.get("held_out") is True
    )
    if not is_candidate:
        return []
    return [
        {
            "rule": "capability-publication-evidence-incomplete",
            "scope": "run",
            "severity": "block",
            "field": field,
        }
        for field in _capability_publication_failures(manifest)
    ]


def _sanitize_public_value(value: Any, run_id: str) -> Any:
    if isinstance(value, str):
        if not run_id or value == _PUBLICATION_RUN_LABEL:
            return value
        return value.replace(run_id, _PUBLICATION_RUN_LABEL)
    if isinstance(value, list):
        return [_sanitize_public_value(item, run_id) for item in value]
    if isinstance(value, dict):
        return {
            _sanitize_public_value(key, run_id)
            if isinstance(key, str)
            else key: _sanitize_public_value(item, run_id)
            for key, item in value.items()
        }
    return value


def _publication_purpose(manifest: dict[str, Any]) -> str | None:
    """Classify only unambiguous local Splash measurement evidence for export."""
    if (
        manifest.get("suite") not in {"pilot", "core"}
        or manifest.get("evidence_class") != "local_measurement"
        or manifest.get("model_is_splash") is not True
    ):
        return None

    selection_status = manifest.get("selection_status")
    separation = manifest.get("calibration_heldout_separation")
    post_hoc_claimed = selection_status == _POST_HOC_SELECTION or separation == _POST_HOC_SELECTION
    if (
        manifest.get("held_out") is True
        and not post_hoc_claimed
        and not _capability_publication_failures(manifest)
    ):
        return _CAPABILITY_PUBLICATION
    if (
        manifest.get("suite") == "pilot"
        and manifest.get("held_out") is False
        and selection_status == _POST_HOC_SELECTION
        and separation == _POST_HOC_SELECTION
    ):
        return _QUALIFICATION_PUBLICATION
    return None


def _qualification_aggregate(aggregate: Any) -> dict[str, Any]:
    """Remove capability-only measures from a post-hoc qualification export."""
    if not isinstance(aggregate, dict):
        return {}
    return {
        key: value for key, value in aggregate.items() if key in _QUALIFICATION_AGGREGATE_FIELDS
    }


def _add_coding_sandbox_public_evidence(
    payload: dict[str, Any], manifest: dict[str, Any], publication_purpose: str | None
) -> None:
    task_families = manifest.get("task_families")
    includes_coding = isinstance(task_families, list) and any(
        isinstance(item, str) and item.casefold() == "coding" for item in task_families
    )
    if publication_purpose != _CAPABILITY_PUBLICATION or not includes_coding:
        return
    coding_sandbox, failures = _validated_coding_sandbox_evidence(manifest)
    if not failures and coding_sandbox is not None:
        payload["coding_sandbox"] = coding_sandbox


def _publication_payload(
    run_id: str, manifest: dict[str, Any], publication_purpose: str | None
) -> dict[str, Any]:
    aggregate = manifest.get("aggregate") or {}
    is_qualification = publication_purpose == _QUALIFICATION_PUBLICATION
    if is_qualification:
        aggregate = _qualification_aggregate(aggregate)
    model_label = (
        "Splash/Qwen3.8 through local LM Studio"
        if manifest.get("model_is_splash")
        else "local model through LM Studio (not verified as Splash)"
    )
    payload = {
        "schema_version": 1,
        "run_label": _PUBLICATION_RUN_LABEL,
        "evidence_class": manifest.get("evidence_class"),
        "publication_purpose": publication_purpose or "not_eligible",
        "capability_evidence": publication_purpose == _CAPABILITY_PUBLICATION,
        "model": model_label,
        "suite": manifest.get("suite"),
        "task_set": (manifest.get("protocol") or {}).get("task_set"),
        "scorer_version": (manifest.get("protocol") or {}).get("scorer_version"),
        "selection_hash": manifest.get("selection_hash"),
        "aggregate": _sanitize_public_value(aggregate, run_id),
        "primary_objective_status": (
            "blocked"
            if is_qualification
            else manifest.get("primary_objective_status_if_run", "blocked")
        ),
        "historical_comparison_status": (
            "prohibited for post-hoc runtime/scorer qualification evidence"
            if is_qualification
            else "unavailable unless exact benchmark/protocol fields validate"
        ),
        "limitations": _sanitize_public_value(
            manifest.get("limitations", [])
            + (
                [
                    "This post-hoc exploratory export qualifies runtime transport and scorer "
                    "operation only; it is not model capability evidence.",
                    "It cannot support a frontier delta, ranking, equivalence, improvement, "
                    "or historical comparison.",
                ]
                if is_qualification
                else []
            )
            + [
                "Raw prompts, responses, reasoning, timestamps, paths, instance identifiers, "
                "and unallowlisted private hashes are excluded.",
                "This export is staged for review and is not automatically committed or uploaded.",
            ],
            run_id,
        ),
    }
    _add_coding_sandbox_public_evidence(payload, manifest, publication_purpose)
    return cast(dict[str, Any], _sanitize_public_value(payload, run_id))


def prepare_publication(
    run_id: str,
    *,
    dry_run: bool = True,
    root: Path | None = None,
) -> dict[str, Any]:
    """Prepare a fresh allowlisted aggregate export outside Git."""
    repo = root or project_root()
    manifest, _ = load_run(run_id, repo)
    publication_purpose = _publication_purpose(manifest)
    payload = _publication_payload(run_id, manifest, publication_purpose)
    audit = audit_publication(root=repo)
    blockers = list(audit["findings"])
    blockers.extend(_capability_publication_blockers(manifest))
    if publication_purpose is None:
        blockers.append(
            {
                "rule": "non-held-out-result-not-capability-evidence",
                "scope": "run",
                "severity": "block",
            }
        )
    if dry_run:
        return {
            "status": "dry_run_blocked" if blockers else "dry_run_ready_for_human_review",
            "run_id": run_id,
            "allowlisted_fields": sorted(payload),
            "blockers": blockers,
            "preview": payload,
            "writes_performed": False,
            "export_writes_performed": False,
            "private_audit_record_written": True,
        }
    if blockers:
        raise PublicationRefused(
            "publication preparation blocked; inspect the private audit report"
        )
    state = get_state_dir(repo)
    export_id = f"{run_id}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    export_dir = state / "publication_exports" / export_id
    export_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
    json_path = export_dir / "aggregate.json"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown = [
        "# Staged Splash evaluation aggregate",
        "",
        f"- Run label: `{_PUBLICATION_RUN_LABEL}`",
        f"- Model: {payload['model']}",
        f"- Suite: `{payload['suite']}`",
        f"- Publication purpose: `{payload['publication_purpose']}`",
        f"- Capability evidence: `{str(payload['capability_evidence']).lower()}`",
        f"- Primary objective status: `{payload['primary_objective_status']}`",
        f"- Historical comparison: {payload['historical_comparison_status']}",
        "",
        "This file is staged outside Git for explicit review. It has not been published.",
    ]
    markdown_path = export_dir / "README.md"
    markdown_path.write_text("\n".join(markdown) + "\n", encoding="utf-8")
    for path in (json_path, markdown_path):
        os.chmod(path, 0o600)
    return {
        "status": "staged_for_human_review",
        "run_id": run_id,
        "export_id": export_id,
        "export_directory": str(export_dir),
        "automatic_publication": False,
    }
