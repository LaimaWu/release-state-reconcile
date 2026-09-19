from __future__ import annotations

import json

from .models import CandidateReport, Evidence


def _escape(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _evidence(evidence: list[Evidence]) -> str:
    if not evidence:
        return "No evidence (UNKNOWN)"
    rendered = []
    for item in evidence:
        label = f"{item.object_type} {item.object_id}"
        rendered.append(
            f"[{_escape(label)}]({item.source_url}) — {_escape(item.observed_state)}; confidence={item.confidence}"
        )
    return "<br>".join(rendered)


def render_markdown(report: CandidateReport) -> str:
    lines = [
        f"# Release-state reconstruction: {report.repo}#{report.number}",
        "",
        f"- Candidate: [{report.candidate_url}]({report.candidate_url})",
        f"- Observed at: `{report.observed_at}`",
        f"- Configured mainline: `{report.mainline_branch}`",
        f"- Target release branch: `{report.target_release_branch}`",
        f"- Materially useful cross-object result: `{'YES' if report.materially_useful else 'NO'}`",
        "",
        "## Reconstructed state",
        "",
        "| Fact | State | Observation | Evidence |",
        "|---|---|---|---|",
    ]
    for finding in report.findings:
        lines.append(
            f"| {_escape(finding.name)} | `{_escape(finding.status)}` | {_escape(finding.summary)} | {_evidence(finding.evidence)} |"
        )

    lines.extend(
        [
            "",
            "## Related pull requests",
            "",
            "| PR | Relation | Base | State | Merge commit | Relationship evidence |",
            "|---|---|---|---|---|---|",
        ]
    )
    if report.related_pulls:
        for pull in report.related_pulls:
            lines.append(
                f"| [#{pull.number}]({pull.url}) | `{pull.relation}` | `{_escape(pull.base)}` | `{pull.state}` / merged=`{str(pull.merged).lower()}` | `{pull.merge_sha or 'UNKNOWN'}` | {_evidence(pull.relation_evidence)} |"
            )
    else:
        lines.append("| — | `UNKNOWN` | — | — | — | No related PR object was established. |")

    lines.extend(
        [
            "",
            "## Explicit relationships",
            "",
            "| Object | Provider | Scope | Relation | Evidence |",
            "|---|---|---|---|---|",
        ]
    )
    if report.relationships:
        for relationship in report.relationships:
            lines.append(
                f"| [{_escape(relationship.object_id)}]({relationship.url}) | `{_escape(relationship.provider)}` | `{_escape(relationship.scope)}` | `{_escape(relationship.relation)}` | {_evidence(relationship.evidence)} |"
            )
    else:
        lines.append("| — | — | — | `UNKNOWN` | No explicit cross-object relationship was established. |")

    lines.extend(["", "## Contradictions", ""])
    if report.contradictions:
        for finding in report.contradictions:
            lines.append(f"- `{finding.status}` — {finding.summary} {_evidence(finding.evidence)}")
    else:
        lines.append("- None detected by the configured generic rules.")

    lines.extend(["", "## Unknowns and missing links", ""])
    if report.unknowns:
        for finding in report.unknowns:
            lines.append(f"- `{finding.status}` — {finding.summary} {_evidence(finding.evidence)}")
    else:
        lines.append("- None.")

    lines.extend(
        [
            "",
            "## Scope guardrails",
            "",
            "This report reconstructs observable public GitHub state. It does not decide candidate eligibility, change risk, release-note quality, or release approval.",
            "",
        ]
    )
    return "\n".join(lines)


def render_json(report: CandidateReport) -> str:
    return json.dumps(report.to_dict(), indent=2, sort_keys=True)
