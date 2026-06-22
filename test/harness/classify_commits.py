#!/usr/bin/env python3
"""Classify linux commits as useful/useless for the benchmark.

Usage:
    python3 classify_commits.py --repo ../linux [--start 0] [--batch 100]
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path


HERE = Path(__file__).parent
STATE_FILE = HERE / "classification_state.json"
TASKS_DIR = HERE.parent / "tasks"
CLASSIFIED_DIR = HERE.parent / "classified"


def run_git(*args, cwd: Path, check: bool = True) -> str:
    result = subprocess.run(
        ["git"] + list(args), cwd=cwd, capture_output=True, text=True, check=check,
    )
    if not check and result.returncode != 0:
        return ""
    return result.stdout.strip()


def has_parent(repo: Path, commit: str) -> bool:
    """Check if commit has a parent in the visible history."""
    parents = run_git("rev-list", "--parents", "-n", "1", commit, cwd=repo)
    if not parents:
        return False
    parts = parents.split()
    return len(parts) > 1  # first is the commit itself, rest are parents


def get_diff_stat(repo: Path, commit: str) -> tuple[list[str], int, int]:
    """Get files changed and line counts. Returns (files, insertions, deletions)."""
    if not has_parent(repo, commit):
        # Root commit in shallow history - cannot compute meaningful diff
        return [], 0, 0

    stat = run_git("diff", "--stat", "--no-color", f"{commit}^1..{commit}", cwd=repo)
    if not stat:
        return [], 0, 0

    lines = stat.strip().split("\n")
    files = []
    for line in lines[:-1]:
        parts = line.split("|")
        if len(parts) >= 2:
            files.append(parts[0].strip())

    insertions = deletions = 0
    if lines:
        summary = lines[-1]
        m = re.search(r"(\d+) files? changed", summary)
        m2 = re.search(r"(\d+) insertion", summary)
        m3 = re.search(r"(\d+) deletion", summary)
        if m2:
            insertions = int(m2.group(1))
        if m3:
            deletions = int(m3.group(1))

    return files, insertions, deletions


def get_diff(repo: Path, commit: str) -> str:
    """Get the full diff for a commit."""
    if not has_parent(repo, commit):
        return ""
    return run_git("diff", "--no-color", f"{commit}^1..{commit}", cwd=repo) or ""


def categorize(subject: str, files_changed: list[str],
               insertions: int, deletions: int) -> tuple[str, str]:
    """Return (category, reason)."""
    total_lines = insertions + deletions
    subject_lower = subject.lower()

    # Always useless categories
    if subject.startswith("Merge tag") or subject.startswith("Merge branch"):
        return "useless", "merge commit"
    if subject.startswith("Revert"):
        return "useless", "revert"
    if subject.startswith("Linux"):
        return "useless", "version bump"

    # Size filters
    if total_lines > 500:
        return "useless", f"too large ({total_lines} lines)"
    if total_lines < 3:
        return "useless", f"too small ({total_lines} lines)"
    if len(files_changed) > 10:
        return "useless", f"too many files ({len(files_changed)})"

    # Check what files are changed
    only_dt_bindings = all(f.startswith("Documentation/devicetree/bindings/") or
                           f.startswith("dt-bindings/") for f in files_changed) if files_changed else False
    only_docs = all(f.startswith("Documentation/") for f in files_changed) if files_changed else False
    only_selftests = all("selftest" in f for f in files_changed) if files_changed else False
    only_kconfig = all(f.endswith("Kconfig") for f in files_changed) if files_changed else False

    if only_dt_bindings:
        return "useless", "only dt-bindings"
    if only_docs and not any(f.endswith((".c", ".h")) for f in files_changed):
        return "useless", "only documentation"
    if only_selftests:
        return "useless", "only selftests"
    if only_kconfig:
        return "useless", "only Kconfig changes"

    # Check subject patterns for useless
    useless_patterns = [
        (r"fix.*typo", "typo fix"),
        (r"fix.*warning", "warning fix"),
        (r"fix.*comment", "comment fix"),
        (r"fix.*spelling", "spelling fix"),
        (r"fix.*format", "formatting fix"),
        (r"^dt-bindings?", "dt-bindings"),
        (r"add.*\.gitignore", "gitignore"),
        (r"cleanup", "cleanup"),
        (r"cosmetic", "cosmetic"),
        (r"annotate", "annotation"),
        (r"simplify with scoped", "mechanical refactor"),
        (r"simplify", "simplification"),
        (r"use scoped", "mechanical refactor"),
        (r"convert to", "mechanical conversion"),
        (r"add missing \w+ (parenthesis|bracket|semicolon)", "trivial syntax"),
        (r"fix.*double", "trivial fix"),
        (r"update (copyright|license|spdx)", "license change"),
        (r"remove unused", "removal"),
        (r"delete unused", "removal"),
        (r"const.*attribute", "const attribute"),
        (r"__init.*annotation", "init annotation"),
        (r"\.gitignore", "gitignore"),
        (r"fix.*kernel-doc", "kernel-doc fix"),
        (r"fix.*doc", "doc fix"),
        (r"typo", "typo"),
    ]
    for pattern, reason in useless_patterns:
        if re.search(pattern, subject_lower):
            return "useless", reason

    # Check if files are mostly trivial
    trivial_extensions = {".json", ".yaml", ".dts", ".dtsi", ".rst", ".txt", ".png"}
    c_files = [f for f in files_changed if f.endswith((".c", ".h", ".S", ".s"))]
    if not c_files and files_changed:
        return "useless", "no C code changes"

    # Useful patterns
    useful_patterns = [
        (r"fix.*(race|lock|deadlock|crash|leak|overflow|corruption|null|use-after-free|oom|infinite|hang|bypass|vuln|security|cve|regression|dead.code|logic|data.race|error.path|refcounting)", "bug fix"),
        (r"support", "feature"),
        (r"implement", "feature"),
        (r"add\s+(new\s+)?(driver|support|handling|check|validation|parameter|option|flag|register|function|interface|command|ioctl|sysfs)", "feature"),
        (r"enable", "feature"),
        (r"introduce", "feature"),
        (r"improve", "improvement"),
        (r"optimize|performance", "optimization"),
        (r"rework", "rework"),
        (r"refactor", "refactor"),
        (r"handle", "logic change"),
        (r"prevent", "bug fix"),
        (r"avoid", "bug fix"),
        (r"correct", "bug fix"),
        (r"reset", "reset logic"),
        (r"workaround", "workaround"),
        (r"restore", "restoration"),
        (r"replace.*with", "replacement"),
        (r"propagate", "propagation"),
        (r"respect", "respect flag/option"),
        (r"fall back", "fallback logic"),
        (r"retry", "retry logic"),
    ]
    for pattern, reason in useful_patterns:
        if re.search(pattern, subject_lower):
            return "useful", reason

    # Default: check if it's a real code change
    if c_files and total_lines > 10:
        return "maybe", f"code change ({len(c_files)} files, {total_lines} lines)"

    return "maybe", f"uncategorized ({len(files_changed)} files, {total_lines} lines)"


def generate_task_description(subject: str, body: str) -> str:
    """Generate a detailed task ticket from the commit info."""
    if ":" in subject:
        subsystem = subject.split(":")[0]
        desc = subject.split(":", 1)[1].strip()
    else:
        subsystem = "kernel"
        desc = subject

    desc_lower = desc.lower()

    if any(w in desc_lower for w in ["fix", "bug", "avoid", "prevent", "correct", "workaround"]):
        task_type = "Bug Fix"
    elif any(w in desc_lower for w in ["add", "support", "implement", "introduce", "enable"]):
        task_type = "Feature Implementation"
    elif any(w in desc_lower for w in ["refactor", "rework", "simplify"]):
        task_type = "Refactoring"
    elif any(w in desc_lower for w in ["optimize", "improve", "performance"]):
        task_type = "Optimization"
    else:
        task_type = "Code Change"

    # Clean up body - remove Signed-off-by and other tags
    body_lines = []
    for line in body.split("\n"):
        if line.startswith("Signed-off-by"):
            continue
        if line.startswith("Fixes:"):
            continue
        if line.startswith("Reported-by"):
            continue
        if line.startswith("Reviewed-by"):
            continue
        if line.startswith("Tested-by"):
            continue
        if line.startswith("Suggested-by"):
            continue
        if line.startswith("Acked-by"):
            continue
        if line.startswith("Cc:"):
            continue
        if line.startswith("Link:"):
            continue
        if line.startswith("Closes:"):
            continue
        body_lines.append(line)

    body_clean = "\n".join(body_lines).strip()

    prompt = f"""# {task_type}: {subject}

## Description
The Linux kernel {subsystem} subsystem needs the following change implemented:

{desc}

{body_clean}

## Requirements
1. Implement the necessary changes to achieve the described behavior
2. Follow Linux kernel coding style and conventions
3. Ensure proper error handling and edge cases
4. Add appropriate kernel-doc comments for any new functions or structures
5. Ensure compilation with no new warnings

## Acceptance Criteria
- The change compiles without errors or warnings for the relevant subsystem
- All existing functionality is preserved
- The implementation follows the patterns already established in the surrounding code
- Proper lock ordering and memory management are maintained
- Any new interfaces are properly documented

## Notes
- Study the surrounding code carefully to understand the existing patterns
- Make minimal changes to achieve the goal
- Pay attention to the specific subsystem's conventions
- Consider all edge cases and error paths"""

    if task_type == "Bug Fix":
        prompt += """
- Identify the root cause of the issue
- Apply the minimal fix that addresses it
- Add comments explaining why the fix is correct
- Consider if similar issues exist elsewhere in the code"""

    elif task_type == "Feature Implementation":
        prompt += """
- Study existing similar features/drivers in the subsystem for patterns
- Ensure proper integration with the existing infrastructure
- Add appropriate conditional compilation if hardware-dependent"""

    return prompt


def process_commit(repo: Path, commit_hash: str, index: int,
                   auto_useful: bool = False) -> dict | None:
    """Process a single commit. Returns test dict if useful."""
    # Get basic info
    info = run_git("log", "--format=%H%n%ai%n%s%n%an%n%ae", "-1", commit_hash, cwd=repo)
    if not info:
        return {"index": index, "hash": commit_hash, "subject": "UNKNOWN",
                "category": "useless", "reason": "git log failed",
                "files_changed": 0, "total_lines": 0}

    parts = info.split("\n")
    if len(parts) < 5:
        return {"index": index, "hash": commit_hash, "subject": f"PARSE_ERROR:{info[:100]}",
                "category": "useless", "reason": "parse error",
                "files_changed": 0, "total_lines": 0}

    subject = parts[2]

    # Root commits in shallow history are unusable
    if not has_parent(repo, commit_hash):
        return {"index": index, "hash": commit_hash, "date": parts[1],
                "subject": subject, "author": parts[3],
                "category": "useless", "reason": "shallow boundary (no parent)",
                "files_changed": 0, "total_lines": 0}

    files_changed, insertions, deletions = get_diff_stat(repo, commit_hash)
    total_lines = insertions + deletions

    category, reason = categorize(subject, files_changed, insertions, deletions)
    do_useful = (category == "useful") or (category == "maybe" and auto_useful)

    result = {
        "index": index,
        "hash": commit_hash,
        "date": parts[1],
        "subject": subject,
        "author": parts[3],
        "category": category,
        "reason": reason,
        "files_changed": len(files_changed),
        "total_lines": total_lines,
    }

    if do_useful:
        diff = get_diff(repo, commit_hash)
        body = run_git("log", "--format=%b", "-1", commit_hash, cwd=repo) or ""
        prompt = generate_task_description(subject, body)
        test = {
            "commit": commit_hash,
            "date": parts[1],
            "subject": subject,
            "files_changed": files_changed,
            "insertions": insertions,
            "deletions": deletions,
            "total_lines": total_lines,
            "category": category,
            "reason": reason,
            "prompt": prompt,
            "expected_diff": diff,
        }
        result["test"] = test

    return result


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2))


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"last_index": -1, "processed": [], "useful_count": 0, "useless_count": 0}


def main():
    parser = argparse.ArgumentParser(description="Classify linux commits")
    parser.add_argument("--repo", type=Path, required=True, help="Path to linux git repo")
    parser.add_argument("--start", type=int, default=0, help="Start index (0-based)")
    parser.add_argument("--batch", type=int, default=50, help="Number to process")
    parser.add_argument("--auto-useful", action="store_true",
                        help="Auto-accept 'maybe' commits as useful")
    parser.add_argument("--dry-run", action="store_true", help="Don't write tests/state")
    args = parser.parse_args()

    repo = args.repo
    if not (repo / ".git").exists():
        print(f"ERROR: {repo} is not a git repo")
        sys.exit(1)

    print("Getting commit list...")
    commits = run_git("log", "--format=%H", "--reverse", cwd=repo).split("\n")
    print(f"Total commits: {len(commits)}")

    state = load_state() if not args.dry_run else {"last_index": -1, "processed": []}
    start = max(args.start, state["last_index"] + 1)
    end = min(start + args.batch, len(commits))

    tasks_dir = TASKS_DIR
    classified_dir = CLASSIFIED_DIR
    if not args.dry_run:
        tasks_dir.mkdir(parents=True, exist_ok=True)
        classified_dir.mkdir(parents=True, exist_ok=True)

    processed_hashes = set(state.get("processed", []))

    for i in range(start, end):
        commit_hash = commits[i]
        if commit_hash in processed_hashes:
            continue

        print(f"\n[{i+1}/{len(commits)}] {commit_hash[:12]} ", end="", flush=True)
        result = process_commit(repo, commit_hash, i, auto_useful=args.auto_useful)

        category = result["category"]
        reason = result["reason"]
        subject = result.get("subject", "")[:80]
        print(f"[{category:7}] {subject} ({reason})")

        if args.dry_run:
            continue

        # Write classified log (without test/diff to save space)
        log_entry = {k: v for k, v in result.items() if k not in ("test",)}
        classified_file = classified_dir / f"{i:06d}_{commit_hash[:12]}.json"
        classified_file.write_text(json.dumps(log_entry, indent=2))

        # If useful, write test
        if "test" in result:
            test = result["test"]
            test_file = tasks_dir / f"{i:06d}_{commit_hash[:12]}.json"
            test_file.write_text(json.dumps(test, indent=2))
            print(f"    -> TEST: {test_file.name}")

        # Update state
        state["last_index"] = i
        state["processed"].append(commit_hash)
        if "test" in result:
            state["useful_count"] = state.get("useful_count", 0) + 1
        else:
            state["useless_count"] = state.get("useless_count", 0) + 1
        save_state(state)

    print(f"\nDone. Processed {end - start} commits ({start}-{end-1})")
    print(f"  Useful: {state.get('useful_count', 0)}")
    print(f"  Useless: {state.get('useless_count', 0)}")
    print(f"  Last index: {state.get('last_index', -1)}")


if __name__ == "__main__":
    main()
