# Release State Reconcile

Read-only CLI for reconciling release candidates across fixes, backports, checks, release notes, and published releases.

Given one public GitHub repository, one selected release candidate, and one release line, Release State Reconcile reconstructs the public evidence chain across implementation, backports, checks, release-note evidence, and published releases, surfacing missing links, branch mismatches, contradictions, and `UNKNOWN` states without mutating GitHub.

## Why this exists

Release state is often spread across an issue, one or more pull requests, CI checks, changed files, maintenance branches, and release tags. This tool assembles those public facts into one deterministic report and keeps uncertainty explicit. It supports investigation and review; it does not make release decisions.

## Installation

Python 3.10 or newer is required.

### PyPI

Install the stable release from PyPI:

```bash
pip install release-state-reconcile
```

### GitHub Release wheel

Install the verified wheel attached to the `v0.1.0` GitHub Release:

```bash
python -m pip install https://github.com/LaimaWu/release-state-reconcile/releases/download/v0.1.0/release_state_reconcile-0.1.0-py3-none-any.whl
```

### Tagged Git source

Alternatively, install from the exact `v0.1.0` tag:

```bash
python -m pip install "git+https://github.com/LaimaWu/release-state-reconcile.git@v0.1.0"
```

If you use `pipx`, the same tagged source can be installed as an isolated CLI application:

```bash
pipx install "git+https://github.com/LaimaWu/release-state-reconcile.git@v0.1.0"
```

### Contributor and development install

From a source checkout, contributors can retain an editable installation:

```bash
python -m pip install -e .
```

## CLI

```bash
release-state-reconcile \
  --candidate https://github.com/OWNER/REPOSITORY/issues/123 \
  --release-branch release/1.2 \
  --config repository.json \
  --output report.md
```

Use `--format json` for structured output. If `--output` is omitted, the report is written to standard output.

## Output shape

The Markdown report includes an evidence-linked state table, related pull requests, explicit same- and cross-repository relationships, contradictions, and unknown or missing links.

```text
Fact                         State
Original issue/PR            CLOSED
Mainline merge               MERGED | OPEN | UNKNOWN
Required backport PRs        MERGED | MISSING | TARGET_BRANCH_MISMATCH | UNKNOWN
Backport/check state         PASS | FAIL | PENDING | UNKNOWN
Release-note evidence        PRESENT | ABSENT | UNKNOWN
Final tag/release containment CONTAINED | NOT_CONTAINED | UNKNOWN
```

Each observation carries a public source URL, object type, object ID, observed state, and confidence.

## Configuration

Repository conventions live in JSON rather than source code. A minimal configuration looks like this:

```json
{
  "mainline_branch": "main",
  "backport": {"required": true, "search": true},
  "completion": {"closed_is_complete": true, "labels": []},
  "verification_labels": ["verified"],
  "release_note": {"path_globs": ["changes/**"]},
  "release": {"tag_regex": "^v1\\.2\\.[0-9]+$", "max_releases": 10}
}
```

The selected release branch is always supplied explicitly on the command line. Configuration may describe the default branch, whether a backport is expected, repository-specific verification labels, release-note paths, and the release-tag convention.

## Scope and non-goals

The tool observes and reconciles public GitHub state. It does not:

- decide whether a candidate belongs in a release;
- assess change risk or release-note quality;
- approve, merge, label, comment on, or otherwise mutate GitHub objects;
- orchestrate releases across multiple release lines;
- provide end-to-end release automation; or
- use an LLM or make AI-generated release decisions.

## Read-only and security model

The GitHub client exposes only HTTP `GET` operations. Authentication is optional: `GITHUB_TOKEN` or `GH_TOKEN` may be supplied to increase public REST API limits, but the tool has no GitHub write methods. Reports may be written to a local path selected by the caller.

The runtime uses the Python standard library and the public GitHub REST API. Do not place tokens in configuration files or commit them to source control. See [SECURITY.md](SECURITY.md) for vulnerability reporting guidance.

## Validation

The repository contains 21 deterministic unit tests covering closure semantics, relationship discovery, cross-repository preservation, target-branch mismatch handling, external-provider uncertainty, check aggregation, release-note path evidence, and the GET-only client surface. CI runs the full suite on Python 3.10, 3.11, 3.12, and 3.13.

Run locally with:

```bash
python -m unittest discover -s tests -v
```

## Known limitations

- One repository and one selected release line are evaluated per run.
- Repository conventions may require configuration.
- Unsupported external change providers remain `UNKNOWN`.
- Private CI and off-GitHub approvals are outside scope.
- Candidate eligibility and release-note quality remain human judgment.
- Some public issues do not expose enough relationships to reconstruct a useful chain.
- A complete GitHub search establishes only that no qualifying result was returned for that query; it is not proof that an unreferenced change does not exist.

## License

[MIT](LICENSE)
