#!/usr/bin/env python3
"""Pick a suitable commit from linux history for testing.

Selects commits that are old enough to not be in training data,
but not ancient. Filters by diff size and scope.
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path


def run_git(*args, cwd: Path) -> str:
    return subprocess.run(
        ["git"] + list(args),
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def get_commit_info(repo: Path, commit: str) -> dict:
    info = run_git("log", "--format=%H%n%ai%n%s", "-1", commit, cwd=repo)
    lines = info.split("\n")
    return {
        "hash": lines[0],
        "date": lines[1],
        "subject": lines[2],
    }


def pick_task(repo: Path, min_months_old: int = 6, max_months_old: int = 18,
              max_files: int = 10, min_files: int = 1,
              max_diff_lines: int = 500, min_diff_lines: int = 5) -> dict:
    """Pick a random commit from linux history matching criteria."""

    cutoff_old = datetime.now(timezone.utc) - timedelta(days=max_months_old * 30)
    cutoff_recent = datetime.now(timezone.utc) - timedelta(days=min_months_old * 30)

    # Get merge commits (meaningful integration points) in date range
    log = run_git(
        "log", "--merges",
        f"--after={cutoff_old.isoformat()}",
        f"--before={cutoff_recent.isoformat()}",
        "--format=%H %ai",
        "--max-count=500",
        cwd=repo,
    )

    candidates = []
    for line in log.strip().split("\n"):
        if not line:
            continue
        parts = line.split(" ")
        commit_hash = parts[0]

        # Get diff stat to check scope
        try:
            # Use first parent for merge commits
            stat_output = run_git(
                "diff", "--stat", f"{commit_hash}^1..{commit_hash}",
                cwd=repo,
            )
            lines = stat_output.strip().split("\n")
            if len(lines) < 2:
                continue

            # Parse: "X files changed, Y insertions(+), Z deletions(-)"
            summary = lines[-1]
            if "files changed" not in summary:
                continue

            # Count files
            import re
            file_count = int(re.search(r"(\d+) files? changed", summary).group(1))
            if file_count < min_files or file_count > max_files:
                continue
            if "insertion" in summary:
                insertions = int(re.search(r"(\d+) insertion", summary).group(1))
            else:
                insertions = 0
            if "deletion" in summary:
                deletions = int(re.search(r"(\d+) deletion", summary).group(1))
            else:
                deletions = 0
            total_lines = insertions + deletions
            if total_lines < min_diff_lines or total_lines > max_diff_lines:
                continue

        except (subprocess.CalledProcessError, AttributeError, ValueError):
            continue

        info = get_commit_info(repo, commit_hash)
        candidates.append({
            "hash": commit_hash,
            "date": info["date"],
            "subject": info["subject"],
            "files_changed": file_count,
            "total_lines": total_lines,
        })

    if not candidates:
        raise RuntimeError(
            f"No suitable commits found in range "
            f"{cutoff_old.date()} to {cutoff_recent.date()}. "
            f"Try adjusting filters."
        )

    # Pick the most recent suitable commit
    candidate = candidates[0]

    # Get the actual diff for expected output
    diff = run_git("diff", "--no-color", f"{candidate['hash']}^1..{candidate['hash']}", cwd=repo)

    return {
        "commit": candidate["hash"],
        "date": candidate["date"],
        "subject": candidate["subject"],
        "files_changed": candidate["files_changed"],
        "total_lines": candidate["total_lines"],
        "parent": f"{candidate['hash']}^1",
        "description": f"Implement the following change in the Linux kernel: {candidate['subject']}",
        "expected_diff": diff,
    }


def main():
    parser = argparse.ArgumentParser(description="Pick a Linux kernel commit for testing")
    parser.add_argument("--repo", type=Path, required=True, help="Path to linux git repo")
    parser.add_argument("--output", type=Path, default=Path("task.json"), help="Output file")
    parser.add_argument("--min-months", type=int, default=6, help="Min months old")
    parser.add_argument("--max-months", type=int, default=18, help="Max months old")
    parser.add_argument("--max-files", type=int, default=10, help="Max files changed")
    parser.add_argument("--min-files", type=int, default=1, help="Min files changed")
    parser.add_argument("--max-lines", type=int, default=500, help="Max diff lines")
    parser.add_argument("--min-lines", type=int, default=5, help="Min diff lines")
    args = parser.parse_args()

    task = pick_task(
        repo=args.repo,
        min_months_old=args.min_months,
        max_months_old=args.max_months,
        max_files=args.max_files,
        min_files=args.min_files,
        max_diff_lines=args.max_lines,
        min_diff_lines=args.min_lines,
    )

    args.output.write_text(json.dumps(task, indent=2))
    print(f"Task written to {args.output}")
    print(f"  Commit: {task['commit']}")
    print(f"  Date: {task['date']}")
    print(f"  Subject: {task['subject']}")
    print(f"  Files: {task['files_changed']}, Lines: {task['total_lines']}")


if __name__ == "__main__":
    main()
