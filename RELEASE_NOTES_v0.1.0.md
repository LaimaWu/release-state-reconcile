# Release Notes — v0.1.0

Initial public-source release candidate.

## Included

- Deterministic reconstruction of public GitHub release-state evidence for one repository and one selected release line.
- Relationship discovery from candidate bodies, issue comments, and timeline/development events.
- Preservation of same-repository, cross-repository, and unsupported external-provider relationships.
- Mainline, backport, target-branch mismatch, CI check, release-note path, and release containment states.
- Calibrated `UNKNOWN`, missing-link, and contradiction reporting.
- GET-only GitHub client with optional token authentication for rate limits.
- Markdown and JSON report formats.
- 21 deterministic unit tests.

## Known limitations

See the README. In particular, external change providers are not queried, private CI and approvals are outside scope, and candidate eligibility remains a human decision.
