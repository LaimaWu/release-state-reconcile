from __future__ import annotations

import fnmatch
import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote_plus, urlparse

from .github import GitHubAPIError
from .models import CandidateReport, Evidence, Finding, PullRecord, RelationshipRecord


GITHUB_OBJECT_URL_RE = re.compile(
    r"https://github\.com/([^/\s]+)/([^/\s]+)/(?P<kind>pull|issues)/(\d+)", re.I
)
PULL_URL_RE = re.compile(r"https://github\.com/([^/\s]+)/([^/\s]+)/pull/(\d+)", re.I)
URL_RE = re.compile(r"https?://[^\s<>\]\[)\"']+")
EXTERNAL_CHANGE_RE = re.compile(
    r"https?://(?:"
    r"(?:[a-z0-9.-]*gerrit[a-z0-9.-]*|review\.[a-z0-9.-]+|[a-z0-9.-]+\.googlesource\.com)/[^\s<>\]\[)\"']+"
    r"|go\.dev/cl/\d+)" ,
    re.I,
)
CROSS_REPO_REF_RE = re.compile(
    r"(?<![\w/])([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)#(\d+)\b"
)
IMPLEMENTATION_TEXT_RE = re.compile(
    r"\b(?:done|fixed|implemented|resolved|addressed|completed)\s+(?:upstream\s+)?(?:by|in|via)\b",
    re.I,
)
CLOSING_RE_TEMPLATE = (
    r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+"
    r"(?:https://github\.com/{owner}/{repo}/issues/{number}|#{number})\b"
)


def parse_candidate_url(url: str) -> tuple[str, str, str, int]:
    parsed = urlparse(url)
    parts = parsed.path.strip("/").split("/")
    if parsed.netloc.lower() != "github.com" or len(parts) != 4:
        raise ValueError("candidate must be a GitHub issue or pull URL")
    owner, repo, kind, raw_number = parts
    if kind not in {"issues", "pull"} or not raw_number.isdigit():
        raise ValueError("candidate must be a GitHub issue or pull URL")
    return owner, repo, kind, int(raw_number)


def _ev(url: str, kind: str, object_id: str, state: str, confidence: str = "factual") -> Evidence:
    return Evidence(url, kind, object_id, state, confidence)


def _explicit_body_relation(body: str, owner: str, repo: str, number: int) -> str:
    closing = re.compile(
        CLOSING_RE_TEMPLATE.format(owner=re.escape(owner), repo=re.escape(repo), number=number),
        re.I,
    )
    if closing.search(body or ""):
        return "implementation"
    full_issue = f"https://github.com/{owner}/{repo}/issues/{number}"
    if full_issue.lower() in (body or "").lower() or re.search(
        rf"(?<!\w)#{number}\b", body or ""
    ):
        return "explicit_reference"
    return "context_reference"


def _merge_relation(old: str | None, new: str) -> str:
    rank = {
        "candidate_pr": 7,
        "implementation": 6,
        "development_connection": 5,
        "explicit_reference": 4,
        "context_reference": 3,
        "cross_reference": 2,
        "external_implementation": 2,
        "external_reference": 1,
    }
    return new if old is None or rank.get(new, 0) > rank.get(old, 0) else old


def _walk_urls(value: Any):
    """Yield public URLs embedded anywhere in a timeline/development event."""
    if isinstance(value, str):
        yield from (match.group(0).rstrip(".,;:") for match in URL_RE.finditer(value))
    elif isinstance(value, dict):
        for nested in value.values():
            yield from _walk_urls(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk_urls(nested)


def _closure_state(issue: dict[str, Any]) -> tuple[str, str]:
    if issue.get("state") != "closed":
        return "NOT_COMPLETE", "candidate is not closed"
    reason = issue.get("state_reason")
    if reason == "completed":
        return "COMPLETED", "closed with state_reason=completed"
    if reason == "not_planned":
        return "NOT_PLANNED", "closed with state_reason=not_planned"
    return "UNKNOWN", "closed candidate has no unambiguous completion reason"


def _check_state(checks: dict[str, Any]) -> tuple[str, str, list[Evidence]]:
    runs = checks.get("check_runs", [])
    if not runs:
        return "UNKNOWN", "No check runs were returned for the selected commit.", []
    bad = {"failure", "cancelled", "timed_out", "action_required", "startup_failure"}
    allowed = {"success", "neutral", "skipped"}
    conclusions = [run.get("conclusion") for run in runs]
    statuses = [run.get("status") for run in runs]
    evidence = [
        _ev(
            run.get("details_url") or run.get("html_url") or "https://github.com",
            "check_run",
            str(run.get("id", "unknown")),
            f"status={run.get('status')}; conclusion={run.get('conclusion')}",
        )
        for run in runs
    ]
    if any(value in bad for value in conclusions):
        return "FAIL", "At least one check run has a failing conclusion.", evidence
    if any(value != "completed" for value in statuses) or any(value is None for value in conclusions):
        return "PENDING", "At least one check run is not complete.", evidence
    if all(value in allowed for value in conclusions):
        return "PASS", "All returned check runs completed with allowed conclusions.", evidence
    return "UNKNOWN", "Check conclusions did not match a configured deterministic state.", evidence


def reconcile_candidate(
    github: Any,
    candidate_url: str,
    release_branch: str,
    config: dict[str, Any],
    observed_at: str | None = None,
) -> CandidateReport:
    owner, repo, candidate_kind, number = parse_candidate_url(candidate_url)
    repo_name = f"{owner}/{repo}"
    issue = github.issue(owner, repo, number)
    timeline = github.timeline(owner, repo, number)
    comments = github.comments(owner, repo, number)
    mainline_branch = config.get("mainline_branch", "main")
    now = observed_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    candidate_evidence = _ev(
        issue["html_url"],
        "pull_request" if issue.get("pull_request") else "issue",
        f"{repo_name}#{number}",
        f"state={issue.get('state')}; state_reason={issue.get('state_reason')}; labels={','.join(x['name'] for x in issue.get('labels', [])) or 'none'}",
    )
    findings: list[Finding] = [
        Finding(
            "Original issue/PR",
            issue.get("state", "unknown").upper(),
            issue.get("title", ""),
            [candidate_evidence],
        )
    ]
    unknowns: list[Finding] = []
    contradictions: list[Finding] = []

    relation_by_number: dict[int, str] = {}
    relation_evidence: dict[int, list[Evidence]] = defaultdict(list)
    relationship_by_url: dict[str, RelationshipRecord] = {}

    def add_relationship(
        url: str,
        *,
        provider: str,
        repository: str | None,
        object_type: str,
        object_id: str,
        scope: str,
        relation: str,
        evidence: Evidence,
    ) -> None:
        normalized = url.rstrip("/.,;:")
        existing = relationship_by_url.get(normalized)
        if existing:
            existing.relation = _merge_relation(existing.relation, relation)
            if evidence not in existing.evidence:
                existing.evidence.append(evidence)
            return
        relationship_by_url[normalized] = RelationshipRecord(
            url=normalized,
            provider=provider,
            repository=repository,
            object_type=object_type,
            object_id=object_id,
            scope=scope,
            relation=relation,
            evidence=[evidence],
        )

    def discover_text_relationships(
        body: str,
        source_evidence: Evidence,
        *,
        implementation_hint: bool = False,
    ) -> None:
        for match in GITHUB_OBJECT_URL_RE.finditer(body or ""):
            link_owner, link_repo = match.group(1), match.group(2)
            link_kind, raw_number = match.group("kind"), match.group(4)
            link_repo_name = f"{link_owner}/{link_repo}"
            link_number = int(raw_number)
            url = match.group(0).rstrip("/.,;:")
            same_repo = link_repo_name.lower() == repo_name.lower()
            scope = "same_repo" if same_repo else "cross_repo"
            if link_kind == "pull":
                if same_repo:
                    relation = "implementation" if implementation_hint else "context_reference"
                    relation_by_number[link_number] = _merge_relation(
                        relation_by_number.get(link_number), relation
                    )
                    relation_evidence[link_number].append(source_evidence)
                else:
                    relation = "external_implementation" if implementation_hint else "external_reference"
                add_relationship(
                    url,
                    provider="github",
                    repository=link_repo_name,
                    object_type="pull_request",
                    object_id=f"{link_repo_name}#{link_number}",
                    scope=scope,
                    relation=relation,
                    evidence=source_evidence,
                )
            elif not (same_repo and link_number == number):
                add_relationship(
                    url,
                    provider="github",
                    repository=link_repo_name,
                    object_type="issue",
                    object_id=f"{link_repo_name}#{link_number}",
                    scope=scope,
                    relation="external_reference" if not same_repo else "context_reference",
                    evidence=source_evidence,
                )
        for match in EXTERNAL_CHANGE_RE.finditer(body or ""):
            url = match.group(0).rstrip("/.,;:")
            host = urlparse(url).netloc.lower()
            add_relationship(
                url,
                provider="gerrit" if "gerrit" in host or "googlesource" in host or host == "go.dev" else host,
                repository=None,
                object_type="external_change",
                object_id=url,
                scope="external_provider",
                relation="external_implementation" if implementation_hint else "external_reference",
                evidence=source_evidence,
            )
        for match in CROSS_REPO_REF_RE.finditer(body or ""):
            link_repo_name = f"{match.group(1)}/{match.group(2)}"
            if link_repo_name.lower() == repo_name.lower():
                continue
            link_number = int(match.group(3))
            add_relationship(
                f"https://github.com/{link_repo_name}/issues/{link_number}",
                provider="github",
                repository=link_repo_name,
                object_type="issue_or_pull",
                object_id=f"{link_repo_name}#{link_number}",
                scope="cross_repo",
                relation="external_implementation" if implementation_hint else "external_reference",
                evidence=source_evidence,
            )

    if candidate_kind == "pull" or issue.get("pull_request"):
        relation_by_number[number] = "candidate_pr"
        relation_evidence[number].append(candidate_evidence)
        add_relationship(
            issue["html_url"],
            provider="github",
            repository=repo_name,
            object_type="pull_request",
            object_id=f"{repo_name}#{number}",
            scope="same_repo",
            relation="candidate_pr",
            evidence=candidate_evidence,
        )

    issue_body = issue.get("body") or ""
    discover_text_relationships(
        issue_body,
        _ev(issue["html_url"], "candidate_body", f"{repo_name}#{number}", "relationship found in candidate body"),
        implementation_hint=bool(IMPLEMENTATION_TEXT_RE.search(issue_body)),
    )

    for event in timeline:
        event_name = str(event.get("event") or "timeline")
        relation = "development_connection" if event_name in {"connected", "merged"} else "cross_reference"
        for discovered_url in set(_walk_urls(event)):
            match = GITHUB_OBJECT_URL_RE.fullmatch(discovered_url.rstrip("/"))
            if not match:
                continue
            link_repo_name = f"{match.group(1)}/{match.group(2)}"
            link_number = int(match.group(4))
            link_kind = match.group("kind")
            if link_repo_name.lower() == repo_name.lower() and link_number == number:
                continue
            event_evidence = _ev(
                discovered_url,
                f"timeline_{event_name}",
                f"{link_repo_name}#{link_number}",
                f"relationship observed in timeline event at {event.get('created_at')}",
            )
            same_repo = link_repo_name.lower() == repo_name.lower()
            if same_repo and link_kind == "pull":
                relation_by_number[link_number] = _merge_relation(
                    relation_by_number.get(link_number), relation
                )
                relation_evidence[link_number].append(event_evidence)
            add_relationship(
                discovered_url,
                provider="github",
                repository=link_repo_name,
                object_type="pull_request" if link_kind == "pull" else "issue",
                object_id=f"{link_repo_name}#{link_number}",
                scope="same_repo" if same_repo else "cross_repo",
                relation=relation if same_repo else "external_reference",
                evidence=event_evidence,
            )

    extra_patterns = [re.compile(p, re.I) for p in config.get("implementation_comment_patterns", [])]
    for comment in comments:
        body = comment.get("body") or ""
        is_implementation = bool(IMPLEMENTATION_TEXT_RE.search(body)) or any(
            pattern.search(body) for pattern in extra_patterns
        )
        discover_text_relationships(
            body,
            _ev(
                comment["html_url"],
                "issue_comment",
                str(comment.get("id", "unknown")),
                "relationship found in issue comment",
            ),
            implementation_hint=is_implementation,
        )

    pulls: dict[int, PullRecord] = {}

    def load_pull(pull_number: int) -> PullRecord:
        if pull_number in pulls:
            return pulls[pull_number]
        raw = github.pull(owner, repo, pull_number)
        body_relation = _explicit_body_relation(raw.get("body") or "", owner, repo, number)
        relation = _merge_relation(relation_by_number.get(pull_number), body_relation)
        if relation != relation_by_number.get(pull_number):
            relation_evidence[pull_number].append(
                _ev(
                    raw["html_url"],
                    "pull_request",
                    f"{repo_name}#{pull_number}",
                    f"PR body establishes relation={relation}",
                )
            )
        relation_by_number[pull_number] = relation
        add_relationship(
            raw["html_url"],
            provider="github",
            repository=repo_name,
            object_type="pull_request",
            object_id=f"{repo_name}#{pull_number}",
            scope="same_repo",
            relation=relation,
            evidence=relation_evidence[pull_number][-1]
            if relation_evidence[pull_number]
            else _ev(raw["html_url"], "pull_request", f"{repo_name}#{pull_number}", f"relation={relation}"),
        )
        record = PullRecord(
            number=pull_number,
            url=raw["html_url"],
            title=raw.get("title", ""),
            state=raw.get("state", "unknown"),
            merged=bool(raw.get("merged")),
            merged_at=raw.get("merged_at"),
            merge_sha=raw.get("merge_commit_sha") if raw.get("merged") else None,
            head_sha=(raw.get("head") or {}).get("sha"),
            base=(raw.get("base") or {}).get("ref", "unknown"),
            labels=[label["name"] for label in raw.get("labels", [])],
            relation=relation,
            relation_evidence=relation_evidence[pull_number],
            changed_files=int(raw.get("changed_files") or 0),
        )
        pulls[pull_number] = record
        return record

    for pull_number in sorted(relation_by_number):
        load_pull(pull_number)

    backport_cfg = config.get("backport", {})
    backport_required = bool(backport_cfg.get("required", False))
    search_evidence: list[Evidence] = []
    search_complete = False
    if backport_required and backport_cfg.get("search", True):
        reference = str(number)
        search_url = (
            "https://github.com/search?q="
            + quote_plus(f'repo:{repo_name} is:pr base:"{release_branch}" "{reference}"')
            + "&type=pullrequests"
        )
        try:
            result = github.search_pulls(owner, repo, release_branch, reference)
            search_complete = int(result.get("total_count", 0)) <= len(result.get("items", []))
            search_evidence.append(
                _ev(
                    search_url,
                    "pull_request_search",
                    f"{repo_name}:{release_branch}:{reference}",
                    f"total_count={result.get('total_count', 0)}; complete={search_complete}",
                )
            )
            for item in result.get("items", []):
                pull_number = int(item["number"])
                relation_by_number[pull_number] = _merge_relation(
                    relation_by_number.get(pull_number), "cross_reference"
                )
                relation_evidence[pull_number].append(search_evidence[-1])
                load_pull(pull_number)
        except GitHubAPIError as exc:
            search_evidence.append(
                _ev(exc.url, "pull_request_search", f"{repo_name}:{release_branch}:{reference}", str(exc), "unknown")
            )

    implementation_relations = {"candidate_pr", "implementation", "development_connection"}
    mainline_pulls = [
        pull
        for pull in pulls.values()
        if pull.base == mainline_branch and pull.relation in implementation_relations
    ]
    merged_mainline = [pull for pull in mainline_pulls if pull.merged]
    if merged_mainline:
        mainline_status = "MERGED"
        mainline_summary = "; ".join(
            f"#{pull.number} merged as {pull.merge_sha}" for pull in merged_mainline
        )
        mainline_evidence = [
            _ev(
                pull.url,
                "pull_request",
                f"{repo_name}#{pull.number}",
                f"merged_at={pull.merged_at}; base={pull.base}; merge_commit={pull.merge_sha}",
            )
            for pull in merged_mainline
        ]
    elif mainline_pulls:
        states = {pull.state for pull in mainline_pulls}
        mainline_status = "OPEN" if "open" in states else "NOT_MERGED"
        mainline_summary = "; ".join(
            f"#{pull.number} state={pull.state}, merged={pull.merged}" for pull in mainline_pulls
        )
        mainline_evidence = [
            _ev(
                pull.url,
                "pull_request",
                f"{repo_name}#{pull.number}",
                f"state={pull.state}; merged={pull.merged}; base={pull.base}",
            )
            for pull in mainline_pulls
        ]
    else:
        mainline_status = "UNKNOWN"
        mainline_summary = "No relationship establishing an implementation PR on the configured mainline was found."
        mainline_evidence = [
            _ev(candidate_url, "candidate", f"{repo_name}#{number}", mainline_summary, "unknown")
        ]
        unknowns.append(Finding("Mainline implementation link", "UNKNOWN", mainline_summary, mainline_evidence))
    findings.append(Finding("Mainline merge", mainline_status, mainline_summary, mainline_evidence))

    qualifying_backport_relations = {
        "candidate_pr",
        "implementation",
        "development_connection",
        "explicit_reference",
    }
    backport_pulls = [
        pull
        for pull in pulls.values()
        if pull.base == release_branch
        and pull.relation in qualifying_backport_relations
    ]
    wrong_release_branch_pulls = [
        pull
        for pull in pulls.values()
        if pull.merged
        and pull.base not in {release_branch, mainline_branch}
        and pull.relation in qualifying_backport_relations
    ]
    external_changes = [
        relationship
        for relationship in relationship_by_url.values()
        if relationship.object_type == "external_change"
    ]
    if not backport_required:
        backport_status = "NOT_REQUIRED"
        backport_summary = "Configuration does not require a backport for this development case."
        backport_evidence = [
            _ev(candidate_url, "configuration_context", "backport.required", "false")
        ]
    elif backport_pulls:
        if all(pull.merged for pull in backport_pulls):
            backport_status = "MERGED"
        elif any(pull.state == "open" for pull in backport_pulls):
            backport_status = "OPEN"
        else:
            backport_status = "NOT_MERGED"
        backport_summary = "; ".join(
            f"#{pull.number} state={pull.state}, merged={pull.merged}, base={pull.base}"
            for pull in backport_pulls
        )
        backport_evidence = [
            _ev(
                pull.url,
                "pull_request",
                f"{repo_name}#{pull.number}",
                f"state={pull.state}; merged={pull.merged}; base={pull.base}",
            )
            for pull in backport_pulls
        ]
    elif external_changes:
        backport_status = "UNKNOWN"
        backport_summary = (
            "EXTERNAL_CHANGE / UNKNOWN: public evidence points to an external change provider "
            "that this GitHub-only prototype does not query."
        )
        backport_evidence = [
            evidence for relationship in external_changes for evidence in relationship.evidence
        ]
        unknowns.append(Finding("Required backport link", "UNKNOWN", backport_summary, backport_evidence))
    elif wrong_release_branch_pulls:
        backport_status = "TARGET_BRANCH_MISMATCH"
        backport_summary = "; ".join(
            f"#{pull.number} merged on actual branch {pull.base}, not selected target {release_branch}"
            for pull in wrong_release_branch_pulls
        )
        backport_evidence = [
            _ev(
                pull.url,
                "pull_request",
                f"{repo_name}#{pull.number}",
                f"merged={pull.merged}; actual_base={pull.base}; selected_target={release_branch}",
            )
            for pull in wrong_release_branch_pulls
        ] + search_evidence
        unknowns.append(
            Finding("Selected target branch", "TARGET_BRANCH_MISMATCH", backport_summary, backport_evidence)
        )
    elif search_complete:
        backport_status = "MISSING"
        backport_summary = "Required backport search completed with no qualifying PR."
        backport_evidence = search_evidence
        unknowns.append(Finding("Required backport link", "MISSING", backport_summary, backport_evidence))
    else:
        backport_status = "UNKNOWN"
        backport_summary = "A required backport relationship could not be established."
        backport_evidence = search_evidence or [
            _ev(candidate_url, "candidate", f"{repo_name}#{number}", backport_summary, "unknown")
        ]
        unknowns.append(Finding("Required backport link", "UNKNOWN", backport_summary, backport_evidence))
    findings.append(Finding("Required backport PRs", backport_status, backport_summary, backport_evidence))

    if backport_pulls:
        selected_for_checks = [pull for pull in backport_pulls if pull.merged] or backport_pulls
    else:
        selected_for_checks = merged_mainline or mainline_pulls
    check_findings: list[tuple[str, str, list[Evidence]]] = []
    for pull in selected_for_checks:
        if not pull.head_sha:
            check_findings.append(("UNKNOWN", f"#{pull.number} has no discoverable head SHA.", []))
            continue
        try:
            state, summary, evidence = _check_state(github.check_runs(owner, repo, pull.head_sha))
        except GitHubAPIError as exc:
            state, summary, evidence = "UNKNOWN", str(exc), [
                _ev(exc.url, "check_run_collection", pull.head_sha, str(exc), "unknown")
            ]
        if not evidence:
            evidence = [
                _ev(
                    f"{pull.url}/commits/{pull.head_sha}",
                    "pull_request_commit",
                    pull.head_sha,
                    "no check runs returned",
                    "unknown",
                )
            ]
        check_findings.append((state, f"#{pull.number}: {summary}", evidence))
    if not check_findings:
        checks_status = "UNKNOWN"
        checks_summary = "No qualifying backport or mainline PR was available for check evaluation."
        checks_evidence = [_ev(candidate_url, "candidate", f"{repo_name}#{number}", checks_summary, "unknown")]
    else:
        priority = {"FAIL": 4, "PENDING": 3, "UNKNOWN": 2, "PASS": 1}
        checks_status = max((item[0] for item in check_findings), key=priority.get)
        checks_summary = " ".join(item[1] for item in check_findings)
        checks_evidence = [evidence for item in check_findings for evidence in item[2]]
    findings.append(Finding("Backport/check state", checks_status, checks_summary, checks_evidence))

    verification_labels = set(config.get("verification_labels", []))
    observed_labels = set(label["name"] for label in issue.get("labels", []))
    for pull in selected_for_checks:
        observed_labels.update(pull.labels)
    if verification_labels:
        matched_labels = sorted(verification_labels & observed_labels)
        verification_status = "PRESENT" if matched_labels else "ABSENT"
        verification_summary = (
            f"Matched verification labels: {', '.join(matched_labels)}"
            if matched_labels
            else f"None of the configured verification labels were present: {', '.join(sorted(verification_labels))}"
        )
    else:
        verification_status = "UNKNOWN"
        verification_summary = "No verification-label vocabulary was configured."
    verification_evidence = [
        _ev(candidate_url, "candidate_labels", f"{repo_name}#{number}", f"observed={','.join(sorted(observed_labels)) or 'none'}", "factual" if verification_labels else "unknown")
    ]
    findings.append(Finding("Verification state", verification_status, verification_summary, verification_evidence))

    release_note_cfg = config.get("release_note", {})
    path_globs = release_note_cfg.get("path_globs", [])
    relevant_pulls = sorted(selected_for_checks, key=lambda pull: pull.number)
    note_matches: list[Evidence] = []
    note_scans: list[Evidence] = []
    note_scan_complete = True
    if path_globs and relevant_pulls:
        for pull in relevant_pulls:
            files = github.pull_files(owner, repo, pull.number)
            complete = pull.changed_files <= len(files)
            note_scan_complete = note_scan_complete and complete
            note_scans.append(
                _ev(
                    f"{pull.url}/files",
                    "pull_request_files",
                    f"{repo_name}#{pull.number}",
                    f"returned={len(files)}; changed_files={pull.changed_files}; complete={complete}",
                    "factual" if complete else "unknown",
                )
            )
            for changed in files:
                filename = changed.get("filename", "")
                if any(fnmatch.fnmatch(filename, pattern) for pattern in path_globs):
                    note_matches.append(
                        _ev(
                            changed.get("blob_url") or f"{pull.url}/files",
                            "repository_file",
                            filename,
                            f"changed by PR #{pull.number}; matched release-note path pattern",
                        )
                    )
        if note_matches:
            note_status = "PRESENT"
            note_summary = f"Found {len(note_matches)} changed file(s) matching configured release-note paths."
            note_evidence = note_matches
        elif note_scan_complete:
            note_status = "ABSENT"
            note_summary = "Complete PR file lists contained no configured release-note path."
            note_evidence = note_scans
        else:
            note_status = "UNKNOWN"
            note_summary = "PR file enumeration was incomplete, so absence cannot be established."
            note_evidence = note_scans
    else:
        note_status = "UNKNOWN"
        note_summary = "No release-note path configuration or qualifying implementation PR was available."
        note_evidence = [_ev(candidate_url, "candidate", f"{repo_name}#{number}", note_summary, "unknown")]
    findings.append(Finding("Release-note evidence", note_status, note_summary, note_evidence))

    release_cfg = config.get("release", {})
    tag_regex = release_cfg.get("tag_regex")
    exact_tags = release_cfg.get("tag_names", [])
    release_source_pull = next(
        (pull for pull in backport_pulls if pull.merged),
        next((pull for pull in merged_mainline), None),
    )
    if (tag_regex or exact_tags) and release_source_pull and release_source_pull.merge_sha:
        if exact_tags:
            releases = []
            for tag in exact_tags:
                try:
                    releases.append(github.release_by_tag(owner, repo, tag))
                except GitHubAPIError:
                    continue
        else:
            matcher = re.compile(tag_regex)
            releases = [release for release in github.releases(owner, repo) if matcher.search(release.get("tag_name", ""))]
        max_releases = int(release_cfg.get("max_releases", 10))
        release_evidence: list[Evidence] = []
        contained_release: dict[str, Any] | None = None
        for release in releases[:max_releases]:
            tag = release["tag_name"]
            comparison = github.compare(owner, repo, release_source_pull.merge_sha, tag)
            status = comparison.get("status")
            release_evidence.append(
                _ev(
                    release.get("html_url") or f"https://github.com/{repo_name}/releases/tag/{tag}",
                    "release",
                    tag,
                    f"compare_status={status}; merge_commit={release_source_pull.merge_sha}",
                )
            )
            if status in {"ahead", "identical"}:
                contained_release = release
                break
        if contained_release:
            release_status = "CONTAINED"
            release_summary = f"Merge commit is contained in release {contained_release['tag_name']}."
        elif releases:
            release_status = "NOT_CONTAINED"
            release_summary = f"Checked {min(len(releases), max_releases)} configured release tag(s); none contained the merge commit."
        else:
            release_status = "UNKNOWN"
            release_summary = "No published release matched the configured tag pattern."
            release_evidence = [_ev(candidate_url, "candidate", f"{repo_name}#{number}", release_summary, "unknown")]
    else:
        release_status = "UNKNOWN"
        release_summary = "Containment was not discoverable without both a merged PR and a configured release-tag pattern."
        release_evidence = [_ev(candidate_url, "candidate", f"{repo_name}#{number}", release_summary, "unknown")]
    findings.append(Finding("Final tag/release containment", release_status, release_summary, release_evidence))

    closure_status, closure_summary = _closure_state(issue)
    closure_evidence = [
        _ev(
            issue["html_url"],
            "candidate_closure",
            f"{repo_name}#{number}",
            f"state={issue.get('state')}; state_reason={issue.get('state_reason')}",
            "unknown" if closure_status == "UNKNOWN" else "factual",
        )
    ]
    findings.append(Finding("Candidate completion semantics", closure_status, closure_summary, closure_evidence))
    contradiction_eligible = backport_status in {"OPEN", "NOT_MERGED", "MISSING"} or (
        backport_status == "TARGET_BRANCH_MISMATCH" and search_complete
    )
    if closure_status == "COMPLETED" and backport_required and contradiction_eligible:
        contradiction_evidence = [candidate_evidence] + backport_evidence
        contradictions.append(
            Finding(
                "Candidate complete while required backport incomplete",
                "CONTRADICTION",
                f"Candidate is closed as completed, but required backport state is {backport_status}.",
                contradiction_evidence,
            )
        )
    if not mainline_pulls and not backport_pulls and not relationship_by_url:
        missing = Finding(
            "No qualified implementation/release PR",
            "MISSING_LINK",
            "Referenced PRs, if any, did not establish an implementation or release-branch relationship.",
            [candidate_evidence] + [e for pull in pulls.values() for e in pull.relation_evidence],
        )
        unknowns.append(missing)

    cross_object_relation = bool(pulls) or bool(relationship_by_url) or backport_status == "MISSING"
    materially_useful = cross_object_relation and (
        bool(mainline_pulls)
        or bool(backport_pulls)
        or bool(unknowns)
        or bool(contradictions)
    )
    return CandidateReport(
        candidate_url=candidate_url,
        repo=repo_name,
        number=number,
        observed_at=now,
        target_release_branch=release_branch,
        mainline_branch=mainline_branch,
        findings=findings,
        related_pulls=sorted(pulls.values(), key=lambda pull: pull.number),
        relationships=sorted(relationship_by_url.values(), key=lambda relationship: relationship.url),
        contradictions=contradictions,
        unknowns=unknowns,
        materially_useful=materially_useful,
        api_requests=list(getattr(github, "requests", [])),
    )
