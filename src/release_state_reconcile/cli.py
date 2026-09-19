from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .github import GitHubAPIError, GitHubClient
from .reconcile import reconcile_candidate
from .report import render_json, render_markdown


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Reconstruct release-candidate state from public GitHub data (GET-only)."
    )
    result.add_argument("--candidate", required=True, help="Public GitHub issue or PR URL")
    result.add_argument("--release-branch", required=True, help="Target release branch")
    result.add_argument("--config", required=True, type=Path, help="Repository configuration JSON")
    result.add_argument("--format", choices=("markdown", "json"), default="markdown")
    result.add_argument("--output", type=Path, help="Write report to this path instead of stdout")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        config = json.loads(args.config.read_text(encoding="utf-8"))
        report = reconcile_candidate(
            GitHubClient(), args.candidate, args.release_branch, config
        )
        output = render_json(report) if args.format == "json" else render_markdown(report)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(output + "\n", encoding="utf-8")
        else:
            sys.stdout.write(output + "\n")
    except (ValueError, OSError, json.JSONDecodeError, GitHubAPIError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0

