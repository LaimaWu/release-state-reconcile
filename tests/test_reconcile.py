from __future__ import annotations

import unittest
from pathlib import Path

from release_state_reconcile.github import GitHubAPIError, GitHubClient
from release_state_reconcile.reconcile import reconcile_candidate


class FakeGitHub:
    def __init__(
        self,
        *,
        issue_state="open",
        issue_reason="AUTO",
        issue_body="",
        pull=None,
        pulls=None,
        checks=None,
        files=None,
        search=None,
        comments=None,
        timeline=None,
    ):
        self.requests = []
        self._issue = {
            "html_url": "https://github.com/acme/widgets/issues/42",
            "title": "Candidate behavior",
            "state": issue_state,
            "state_reason": ("completed" if issue_state == "closed" else None)
            if issue_reason == "AUTO"
            else issue_reason,
            "body": issue_body,
            "labels": [],
        }
        self._pull = pull
        self._pulls = dict(pulls or {})
        if pull is not None:
            self._pulls[pull["number"]] = pull
        self._checks = checks if checks is not None else {"check_runs": []}
        self._files = files if files is not None else []
        self._search = search if search is not None else {"total_count": 0, "items": []}
        self._comments = comments if comments is not None else []
        self._timeline = timeline

    def issue(self, owner, repo, number):
        return self._issue

    def timeline(self, owner, repo, number):
        if self._timeline is not None:
            return self._timeline
        if self._pull is None:
            return []
        return [
            {
                "event": "cross-referenced",
                "created_at": "2026-01-01T00:00:00Z",
                "source": {
                    "issue": {
                        "html_url": self._pull["html_url"],
                        "number": self._pull["number"],
                        "pull_request": {"url": "api"},
                    }
                },
            }
        ]

    def comments(self, owner, repo, number):
        return self._comments

    def pull(self, owner, repo, number):
        return self._pulls[number]

    def search_pulls(self, owner, repo, base, reference):
        if isinstance(self._search, Exception):
            raise self._search
        return self._search

    def check_runs(self, owner, repo, sha):
        return self._checks

    def pull_files(self, owner, repo, number):
        return self._files

    def releases(self, owner, repo):
        return []

    def compare(self, owner, repo, base, head):
        return {"status": "ahead"}


def make_pull(*, number=77, base="main", state="closed", merged=True, body="Fixes #42", changed_files=1):
    return {
        "number": number,
        "html_url": f"https://github.com/acme/widgets/pull/{number}",
        "title": "Implement candidate behavior",
        "state": state,
        "merged": merged,
        "merged_at": "2026-01-02T00:00:00Z" if merged else None,
        "merge_commit_sha": f"merge{number}" if merged else None,
        "head": {"sha": f"head{number}"},
        "base": {"ref": base},
        "labels": [],
        "body": body,
        "changed_files": changed_files,
    }


def config(*, required=False, release_note=None):
    return {
        "mainline_branch": "main",
        "backport": {"required": required, "search": required},
        "completion": {"closed_is_complete": True},
        "verification_labels": [],
        "release_note": {"path_globs": release_note or []},
        "release": {},
    }


def finding(report, name):
    return next(item for item in report.findings if item.name == name)


class ReconcileTests(unittest.TestCase):
    candidate = "https://github.com/acme/widgets/issues/42"

    def test_merged_mainline_pr(self):
        report = reconcile_candidate(
            FakeGitHub(pull=make_pull()), self.candidate, "release/1.0", config()
        )
        self.assertEqual("MERGED", finding(report, "Mainline merge").status)

    def test_required_backport_missing_after_complete_search(self):
        report = reconcile_candidate(
            FakeGitHub(), self.candidate, "release/1.0", config(required=True)
        )
        self.assertEqual("MISSING", finding(report, "Required backport PRs").status)

    def test_required_backport_unknown_when_search_fails(self):
        error = GitHubAPIError(403, "https://api.github.com/search/issues", "rate limited")
        report = reconcile_candidate(
            FakeGitHub(search=error), self.candidate, "release/1.0", config(required=True)
        )
        self.assertEqual("UNKNOWN", finding(report, "Required backport PRs").status)

    def test_open_backport_is_found(self):
        pull = make_pull(
            base="release/1.0",
            state="open",
            merged=False,
            body="Ref https://github.com/acme/widgets/issues/42",
        )
        report = reconcile_candidate(
            FakeGitHub(pull=pull), self.candidate, "release/1.0", config(required=True)
        )
        self.assertEqual("OPEN", finding(report, "Required backport PRs").status)

    def test_merged_backport_checks_pass(self):
        pull = make_pull(base="release/1.0", body="Fixes #42")
        checks = {
            "check_runs": [
                {
                    "id": 9,
                    "status": "completed",
                    "conclusion": "success",
                    "details_url": "https://github.com/acme/widgets/actions/runs/9",
                }
            ]
        }
        report = reconcile_candidate(
            FakeGitHub(pull=pull, checks=checks),
            self.candidate,
            "release/1.0",
            config(required=True),
        )
        self.assertEqual("MERGED", finding(report, "Required backport PRs").status)
        self.assertEqual("PASS", finding(report, "Backport/check state").status)

    def test_release_note_present_and_absent(self):
        pull = make_pull()
        present = reconcile_candidate(
            FakeGitHub(
                pull=pull,
                files=[
                    {
                        "filename": "notes/42.md",
                        "blob_url": "https://github.com/acme/widgets/blob/head77/notes/42.md",
                    }
                ],
            ),
            self.candidate,
            "release/1.0",
            config(release_note=["notes/*.md"]),
        )
        absent = reconcile_candidate(
            FakeGitHub(
                pull=pull,
                files=[
                    {
                        "filename": "src/widget.py",
                        "blob_url": "https://github.com/acme/widgets/blob/head77/src/widget.py",
                    }
                ],
            ),
            self.candidate,
            "release/1.0",
            config(release_note=["notes/*.md"]),
        )
        self.assertEqual("PRESENT", finding(present, "Release-note evidence").status)
        self.assertEqual("ABSENT", finding(absent, "Release-note evidence").status)

    def test_complete_candidate_with_open_backport_is_contradiction(self):
        pull = make_pull(
            base="release/1.0",
            state="open",
            merged=False,
            body="Ref https://github.com/acme/widgets/issues/42",
        )
        report = reconcile_candidate(
            FakeGitHub(issue_state="closed", pull=pull),
            self.candidate,
            "release/1.0",
            config(required=True),
        )
        self.assertTrue(any(item.status == "CONTRADICTION" for item in report.contradictions))

    def test_closed_not_planned_never_causes_completion_contradiction(self):
        report = reconcile_candidate(
            FakeGitHub(issue_state="closed", issue_reason="not_planned"),
            self.candidate,
            "release/1.0",
            config(required=True),
        )
        self.assertEqual("NOT_PLANNED", finding(report, "Candidate completion semantics").status)
        self.assertEqual("MISSING", finding(report, "Required backport PRs").status)
        self.assertFalse(report.contradictions)

    def test_closed_completed_with_exhaustive_missing_backport_allows_contradiction(self):
        report = reconcile_candidate(
            FakeGitHub(issue_state="closed", issue_reason="completed"),
            self.candidate,
            "release/1.0",
            config(required=True),
        )
        self.assertEqual("COMPLETED", finding(report, "Candidate completion semantics").status)
        self.assertEqual("MISSING", finding(report, "Required backport PRs").status)
        self.assertEqual(1, len(report.contradictions))

    def test_unclear_closed_reason_prefers_unknown_over_contradiction(self):
        report = reconcile_candidate(
            FakeGitHub(issue_state="closed", issue_reason=None),
            self.candidate,
            "release/1.0",
            config(required=True),
        )
        self.assertEqual("UNKNOWN", finding(report, "Candidate completion semantics").status)
        self.assertFalse(report.contradictions)

    def test_issue_body_same_repo_pr_link_is_discovered(self):
        pull = make_pull(body="Fixes #42")
        report = reconcile_candidate(
            FakeGitHub(
                issue_body="Implemented in https://github.com/acme/widgets/pull/77",
                pull=pull,
                timeline=[],
            ),
            self.candidate,
            "release/1.0",
            config(),
        )
        self.assertEqual([77], [item.number for item in report.related_pulls])
        self.assertTrue(any(item.url.endswith("/pull/77") for item in report.relationships))

    def test_issue_comment_pr_link_is_discovered(self):
        pull = make_pull(base="release/1.0", body="Backport of the candidate change")
        comments = [
            {
                "id": 5,
                "html_url": "https://github.com/acme/widgets/issues/42#issuecomment-5",
                "body": "Completed via https://github.com/acme/widgets/pull/77",
            }
        ]
        report = reconcile_candidate(
            FakeGitHub(pull=pull, comments=comments, timeline=[]),
            self.candidate,
            "release/1.0",
            config(required=True),
        )
        self.assertEqual("MERGED", finding(report, "Required backport PRs").status)

    def test_issue_body_cross_repo_pr_is_preserved_as_external_relationship(self):
        report = reconcile_candidate(
            FakeGitHub(issue_body="Related: https://github.com/outside/tools/pull/9"),
            self.candidate,
            "release/1.0",
            config(),
        )
        external = next(item for item in report.relationships if item.repository == "outside/tools")
        self.assertEqual("cross_repo", external.scope)
        self.assertEqual("pull_request", external.object_type)
        self.assertEqual([], report.related_pulls)

    def test_cross_repo_shorthand_is_preserved_as_external_relationship(self):
        report = reconcile_candidate(
            FakeGitHub(issue_body="Fixed upstream in outside/tools#9"),
            self.candidate,
            "release/1.0",
            config(),
        )
        external = next(item for item in report.relationships if item.repository == "outside/tools")
        self.assertEqual("cross_repo", external.scope)
        self.assertEqual("issue_or_pull", external.object_type)
        self.assertEqual("external_implementation", external.relation)

    def test_merged_pr_on_wrong_release_branch_is_target_branch_mismatch(self):
        pull = make_pull(base="release/2.0", body="Fixes #42")
        report = reconcile_candidate(
            FakeGitHub(
                issue_body="Implemented by https://github.com/acme/widgets/pull/77",
                pull=pull,
                timeline=[],
            ),
            self.candidate,
            "release/1.0",
            config(required=True),
        )
        backport = finding(report, "Required backport PRs")
        self.assertEqual("TARGET_BRANCH_MISMATCH", backport.status)
        self.assertIn("release/2.0", backport.summary)
        self.assertNotEqual("MERGED", backport.status)

    def test_branch_mismatch_needs_complete_search_for_hard_contradiction(self):
        pull = make_pull(base="release/2.0", body="Fixes #42")
        incomplete_search = {"total_count": 2, "items": []}
        report = reconcile_candidate(
            FakeGitHub(
                issue_state="closed",
                issue_reason="completed",
                issue_body="Implemented by https://github.com/acme/widgets/pull/77",
                pull=pull,
                timeline=[],
                search=incomplete_search,
            ),
            self.candidate,
            "release/1.0",
            config(required=True),
        )
        self.assertEqual("TARGET_BRANCH_MISMATCH", finding(report, "Required backport PRs").status)
        self.assertFalse(report.contradictions)

    def test_external_change_with_no_github_target_pr_remains_unknown(self):
        report = reconcile_candidate(
            FakeGitHub(
                issue_body="Implemented via https://review.example.org/c/widgets/+/123"
            ),
            self.candidate,
            "release/1.0",
            config(required=True),
        )
        backport = finding(report, "Required backport PRs")
        self.assertEqual("UNKNOWN", backport.status)
        self.assertIn("EXTERNAL_CHANGE", backport.summary)
        self.assertFalse(report.contradictions)
        self.assertTrue(any(item.object_type == "external_change" for item in report.relationships))

    def test_healthy_mainline_and_backport_chains_remain_supported(self):
        mainline = make_pull(number=70, base="main", body="Fixes #42")
        backport = make_pull(number=71, base="release/1.0", body="Fixes #42")
        timeline = [
            {
                "event": "connected",
                "created_at": "2026-01-01T00:00:00Z",
                "subject": {"url": mainline["html_url"]},
            },
            {
                "event": "connected",
                "created_at": "2026-01-02T00:00:00Z",
                "subject": {"url": backport["html_url"]},
            },
        ]
        report = reconcile_candidate(
            FakeGitHub(pulls={70: mainline, 71: backport}, timeline=timeline),
            self.candidate,
            "release/1.0",
            config(required=True),
        )
        self.assertEqual("MERGED", finding(report, "Mainline merge").status)
        self.assertEqual("MERGED", finding(report, "Required backport PRs").status)

    def test_arbitrary_repository_requires_no_code_change(self):
        report = reconcile_candidate(
            FakeGitHub(pull=make_pull()), self.candidate, "release/1.0", config()
        )
        self.assertEqual("acme/widgets", report.repo)
        self.assertEqual(42, report.number)
        self.assertEqual(77, report.related_pulls[0].number)

    def test_repository_names_are_not_hard_coded_in_package(self):
        package = Path(__file__).parents[1] / "src" / "release_state_reconcile"
        source = "\n".join(path.read_text(encoding="utf-8") for path in package.glob("*.py"))
        for repository in (
            "example-alpha/project-one",
            "example-beta/project-two",
            "example-gamma/project-three",
            "example-delta/project-four",
        ):
            self.assertNotIn(repository, source)

    def test_github_client_has_no_write_methods(self):
        public_names = {name.lower() for name in dir(GitHubClient) if not name.startswith("_")}
        self.assertTrue({"issue", "timeline", "comments", "pull"} <= public_names)
        self.assertFalse({"post", "put", "patch", "delete"} & public_names)


if __name__ == "__main__":
    unittest.main()
