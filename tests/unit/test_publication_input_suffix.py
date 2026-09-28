from __future__ import annotations

from pathlib import Path

import local_evals.publication as publication


def test_publication_scan_checks_sensitive_content_in_dot_in_files(tmp_path: Path) -> None:
    policies = {
        "publication": {
            "max_file_bytes": 100_000,
            "allowed_suffixes": [".md"],
            "denied_suffixes": [".zip", ".tar", ".gz"],
        }
    }
    email = "synthetic.operator" + "@example.invalid"
    private_path = "/" + "Users/synthetic-operator/private-project/result.json"
    candidate = tmp_path / "control-plane-requirements.in"
    candidate.write_text(f"Contact: {email}\nLocation: {private_path}\n", encoding="utf-8")

    findings = publication._scan_file(candidate, tmp_path, policies)

    rules = {finding["rule"] for finding in findings}
    assert "email-address" in rules
    assert "absolute-user-path" in rules
    assert "uninspectable-or-denied-type" not in rules
    assert email not in repr(findings)
    assert private_path not in repr(findings)
