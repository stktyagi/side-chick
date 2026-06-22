#!/usr/bin/env python3
"""Prepare linux source for testing: checkout parent commit, strip .git, save expected.

Usage:
    python3 prepare_source.py --repo /path/to/linux --task task.json --output /path/to/source_dir
"""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


def run_git(*args, cwd: Path) -> str:
    return subprocess.run(
        ["git"] + list(args),
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def prepare_source(repo: Path, task_file: Path, output_dir: Path):
    with open(task_file) as f:
        task = json.load(f)

    parent = task["parent"]
    commit_hash = task["commit"]

    if output_dir.exists():
        subprocess.run(["rm", "-rf", str(output_dir)], check=True)

    output_dir.mkdir(parents=True, exist_ok=True)

    # Checkout parent commit using git archive (faster, no .git)
    print(f"Checking out parent {parent}...")
    with subprocess.Popen(
        ["git", "archive", "--format=tar", parent],
        cwd=repo,
        stdout=subprocess.PIPE,
    ) as proc:
        subprocess.run(
            ["tar", "-xf", "-", "-C", str(output_dir)],
            stdin=proc.stdout,
            check=True,
        )

    # Init a fresh .git in output so opencode can track its own changes
    # (no remote, no history - just a clean slate)
    subprocess.run(["git", "init"], cwd=output_dir, check=True, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=output_dir, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "Initial state (before change)"],
        cwd=output_dir, check=True, capture_output=True,
    )

    # Save expected diff
    expected_diff = run_git("diff", "--no-color", f"{parent}..{commit_hash}", cwd=repo)
    expected_file = output_dir / ".expected_diff"
    expected_file.write_text(expected_diff)

    # Save task info
    task_info = {
        "commit": commit_hash,
        "subject": task["subject"],
        "description": task["description"],
    }
    task_info_file = output_dir / ".task_info.json"
    task_info_file.write_text(json.dumps(task_info, indent=2))

    print(f"Source prepared at {output_dir}")
    print(f"  Expected diff saved to {expected_file}")
    print(f"  Task: {task['subject']}")
    print(f"  Files changed: {task['files_changed']}")


def main():
    parser = argparse.ArgumentParser(description="Prepare linux source for testing")
    parser.add_argument("--repo", type=Path, required=True, help="Path to linux git repo")
    parser.add_argument("--task", type=Path, required=True, help="Task JSON from pick_task.py")
    parser.add_argument("--output", type=Path, required=True, help="Output source directory")
    args = parser.parse_args()

    prepare_source(args.repo, args.task, args.output)


if __name__ == "__main__":
    main()
