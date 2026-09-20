# Security policy

Do not report vulnerabilities, credentials, private paths, raw model outputs, or restricted
benchmark content in a public issue.

Use GitHub's **private vulnerability reporting** feature for this repository. If the
feature is not shown on the Security tab, do not publish the report; ask an organization
owner through an already-established private channel to enable it. This repository does
not publish a fallback email address.

Supported security updates target the latest release and the default branch. Reports
should contain a synthetic reproduction, affected version, impact, and proposed mitigation.
Do not use a real secret as a scanner test.

Private vulnerability reporting must be verified after repository creation; the checked-in
policy alone does not prove that it is enabled.

## Known unresolved optional-dependency vulnerability

Reviewed 2026-09-20: the optional `evalscope` extra locks EvalScope 1.12.0 and brings in
NLTK 3.10.3, both directly and through `rouge-score`. NLTK is affected by
[GHSA-8mgp-746c-j5xp](https://github.com/advisories/GHSA-8mgp-746c-j5xp)
(high severity). The advisory currently lists no patched release. The optional dashboard
is retained by maintainer decision; the Dependabot alert remains open. This is an
unresolved risk, not a fixed or dismissed finding.

The advisory concerns model-artifact import/export APIs bypassing NLTK's path-security
checks when callers supply paths. Do not pass untrusted model artifacts or caller-selected
paths into these APIs, and do not rely on NLTK `pathsec` as a sandbox. Keep the optional
dashboard loopback-only and away from untrusted inputs. These restrictions reduce exposure;
they do not patch the library or prove that every transitive call path is safe.

The core runner and normal development/CI installation do not require the `evalscope`
extra. Leave it uninstalled when the local dashboard is not needed. The public documentation
site is static and does not run EvalScope. Reassess the alert before installing or using the
optional dashboard and when an upstream fix becomes available; validate the fixed dependency
graph before closing it.

The development dependency floor for pytest is 9.0.3, the patched version for
[GHSA-6w46-j5rx-g56g](https://github.com/advisories/GHSA-6w46-j5rx-g56g).
