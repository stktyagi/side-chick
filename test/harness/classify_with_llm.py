#!/usr/bin/env python3
"""Classify linux commits using deepseek-v4-flash-free via opencode zen API.

Sends multiple commits per API call (batched) to reduce requests.
Resumable -- saves state after every API call.
"""

import argparse
import json
import re
import subprocess
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

HERE = Path(__file__).parent
TEST_ROOT = HERE.parent

API_URL = "https://opencode.ai/zen/v1/chat/completions"
API_KEY = "sk-6JSmAIjfLH1qzFLCwYmEEmY02owIjyUnVIFkNPFAcPvS6MCv61EAJLkl6OPlAVxb"
MODEL = "deepseek-v4-flash-free"

CHECKPOINT = TEST_ROOT / "llm_checkpoint.json"
TASKS_DIR = TEST_ROOT / "tasks_llm"
LOGS_DIR = TEST_ROOT / "llm_logs"

MAX_DIFF_CHARS = 1000   # diff chars sent per commit
BATCH_SIZE = 5          # commits per API call (small for reliability)
REQ_DELAY = 0.3         # seconds between API calls


def cmd(*a, cwd=None, timeout=30, check=True):
    r = subprocess.run(list(a), cwd=cwd, capture_output=True, text=True,
                       timeout=timeout, check=False)
    if check and r.returncode != 0:
        raise subprocess.CalledProcessError(r.returncode, list(a),
                                            output=r.stdout, stderr=r.stderr)
    return r.stdout.strip()


def has_parent(repo, commithash):
    return bool(cmd("git", "rev-parse", "-q", "--verify", f"{commithash}^1",
                    cwd=repo, check=False))


def get_commit_info(repo, commithash):
    info = cmd("git", "log", "--format=%H%n%ai%n%s%n%an%n%ae%n%b", "-1",
               commithash, cwd=repo)
    parts = info.split("\n")
    if len(parts) < 5:
        return None
    subject = parts[2]
    body = "\n".join(parts[5:]) if len(parts) > 5 else ""

    stat = cmd("git", "diff", "--stat", "--no-color",
               f"{commithash}^1..{commithash}", cwd=repo, check=False)
    files = []; ins = dels = 0
    if stat:
        for line in stat.split("\n")[:-1]:
            p = line.split("|"); 
            if len(p) >= 2: files.append(p[0].strip())
        last = stat.split("\n")[-1]
        m_i = re.search(r"(\d+) insertion", last)
        m_d = re.search(r"(\d+) deletion", last)
        if m_i: ins = int(m_i.group(1))
        if m_d: dels = int(m_d.group(1))

    diff = cmd("git", "diff", "--no-color", f"{commithash}^1..{commithash}",
               cwd=repo, check=False) or ""
    # Filter to .c/.h/.S only, exclude test/tools
    flines = []; keep = False
    for line in diff.split("\n"):
        if line.startswith("diff --git"):
            keep = False
            m = re.search(r' b/(.+)$', line)
            if m:
                fp = m.group(1)
                ext = Path(fp).suffix
                if ext in ('.c', '.h', '.S', '.s') and \
                   not re.search(r'(selftest|kunit|test|samples|tools|scripts)', fp):
                    keep = True
        if keep:
            flines.append(line)
    diff_f = "\n".join(flines)
    if len(diff_f) > MAX_DIFF_CHARS:
        diff_f = diff_f[:MAX_DIFF_CHARS] + "\n... [truncated]"

    return {
        "subject": subject, "body": body, "date": parts[1],
        "author": parts[3], "files": files,
        "insertions": ins, "deletions": dels, "total": ins + dels,
        "diff": diff_f,
    }


SYSTEM_PROMPT = """You classify Linux kernel commits for an AI benchmark.
Given a list of commits (each with subject, files changed, line count, and diff),
classify each as USEFUL or USELESS.

USEFUL criteria:
- Bug fix with real logic change (race, leak, crash, deadlock, security, logic error, null ptr)
- Feature / new driver / new functionality
- Non-trivial refactoring that changes behavior
- Has .c/.h changes, 3-500 lines, self-contained

USELESS criteria:
- Merge commit, revert, version bump
- Only dt-bindings, docs, selftests, Kconfig, dts, yaml, json, Makefile
- Typo, comment, spelling, formatting, kernel-doc fixes
- < 3 lines or > 500 lines
- Mechanical/trivial: scoped for each, const, __init, static, rename
- No .c/.h changes
- Device ID / quirk additions only

Respond with a JSON array, one object per commit in same order:
[
  {"decision":"useful","reason":"short reason","ticket":"IF useful: detailed dev task. NO file/function names. Describe WHAT, not where. Omit if useless."},
  {"decision":"useless","reason":"short reason","ticket":""}
]

Be generous: mark USEFUL for any real code change. Only mark USELESS if truly trivial."""


def call_llm(prompt):
    for attempt in range(5):
        try:
            payload = json.dumps({
                "model": MODEL,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": 4096,
                "temperature": 0.1,
            }).encode()
            req = urllib.request.Request(
                API_URL, data=payload,
                headers={
                    "Authorization": f"Bearer {API_KEY}",
                    "Content-Type": "application/json",
                    "User-Agent": "curl/8.12.1",
                }, method="POST",
            )
            with urllib.request.urlopen(req, timeout=180) as resp:
                result = json.loads(resp.read())
            content = result["choices"][0]["message"]["content"].strip()
            if content:
                return content
            # Empty response - retry
            if attempt < 4:
                time.sleep(10 * (2 ** attempt))
                continue
            return "ERROR: empty response"
        except urllib.error.HTTPError as e:
            if e.code == 403:
                wait = 30 + (10 * attempt)
                time.sleep(wait)
                continue
            if attempt < 3:
                time.sleep(10 * (2 ** attempt))
                continue
            return f"ERROR: HTTP {e.code}"
        except Exception as e:
            if attempt < 3:
                time.sleep(5 * (2 ** attempt))
                continue
            return f"ERROR: {e}"
    return "ERROR: all retries exhausted"


def build_batch_prompt(commits_info):
    """Build a prompt with multiple commits for batch classification."""
    lines = []
    for i, ci in enumerate(commits_info):
        lines.append(f"--- COMMIT {i} ---")
        lines.append(f"Subject: {ci['subject']}")
        lines.append(f"Files ({len(ci['files'])}): {', '.join(ci['files'][:10])}")
        if len(ci['files']) > 10:
            lines.append(f"  ... and {len(ci['files'])-10} more")
        lines.append(f"Lines: {ci['total']}")
        if ci['diff'] and ci['total'] <= 500:
            lines.append(f"Diff:\n```diff\n{ci['diff']}\n```")
        lines.append("")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", type=Path, required=True)
    ap.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    ap.add_argument("--fresh", action="store_true")
    ap.add_argument("--delay", type=float, default=REQ_DELAY)
    args = ap.parse_args()

    repo = args.repo
    TASKS_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    print("Loading commits...", flush=True)
    all_commits = cmd("git", "log", "--format=%H", "--reverse", cwd=repo).split("\n")
    total = len(all_commits)
    print(f"Total: {total}", flush=True)

    state = {}
    if not args.fresh and CHECKPOINT.exists():
        try:
            state = json.loads(CHECKPOINT.read_text())
        except Exception:
            pass

    start = state.get("last_idx", -1) + 1
    if start >= total:
        print("All done.", flush=True)
        return

    print(f"Starting from index {start}", flush=True)
    batch_i = 0

    while start < total:
        end = min(start + args.batch_size, total)
        print(f"\nBatch {batch_i}: {start}→{end-1} ({end-start} commits)", flush=True)

        # Pre-filter commits in this batch
        batch_commits = []
        for i in range(start, end):
            ch = all_commits[i]
            if not has_parent(repo, ch):
                state["last_idx"] = i
                state.setdefault("skipped", 0)
                state["skipped"] += 1
                continue

            ci = get_commit_info(repo, ch)
            if ci is None:
                state["last_idx"] = i
                continue

            # Quick pre-filter for obvious non-code / tiny / huge
            ext_codes = {'.c', '.h', '.S', '.s'}
            c_files = [f for f in ci["files"] if Path(f).suffix in ext_codes]
            if ci["files"] and not c_files:
                state["last_idx"] = i
                state.setdefault("useless_pre", 0)
                state["useless_pre"] += 1
                continue
            if ci["total"] < 3 or ci["total"] > 500:
                state["last_idx"] = i
                state.setdefault("useless_pre", 0)
                state["useless_pre"] += 1
                continue

            ci["_hash"] = ch
            ci["_index"] = i
            batch_commits.append(ci)

        if not batch_commits:
            start = end
            CHECKPOINT.write_text(json.dumps(state))
            continue

        # Send batch to LLM
        prompt = build_batch_prompt(batch_commits)
        result = call_llm(prompt)

        # Parse results
        parsed = []
        if not result or result.startswith("ERROR:"):
            err = result or "empty response"
            print(f"\nFATAL: API error — {err}", flush=True)
            print(f"Prompt size: {len(prompt)} chars, batch: {start}→{end-1}", flush=True)
            sys.exit(1)
        else:
            try:
                # Extract JSON array: first [ to last ]
                js_start = result.find("[")
                js_end = result.rfind("]")
                if js_start >= 0 and js_end > js_start:
                    parsed = json.loads(result[js_start:js_end+1])
                else:
                    parsed = json.loads(result)
                if not isinstance(parsed, list):
                    parsed = [parsed]
            except (json.JSONDecodeError, Exception) as e:
                print(f"\nFATAL: Parse error — {e}", flush=True)
                print(f"Response preview: {result[:500]}", flush=True)
                print(f"Prompt size: {len(prompt)} chars, batch: {start}→{end-1}", flush=True)
                sys.exit(1)

        for idx, ci in enumerate(batch_commits):
            i = ci["_index"]
            ch_hash = ci["_hash"]
            if idx < len(parsed) and isinstance(parsed[idx], dict):
                dec = parsed[idx].get("decision", "useless")
                reason = parsed[idx].get("reason", "?")
                ticket = parsed[idx].get("ticket", "")
            else:
                dec = "useless"
                reason = "parse-fallback"
                ticket = ""

            tag = "USEFUL" if dec == "useful" else "  -  "
            print(f"  [{tag}] {i:05d} {ch_hash[:12]} {reason:20s} {ci['subject'][:50]}", flush=True)

            if dec == "useful" and ticket:
                test = {
                    "commit": ch_hash,
                    "parent": f"{ch_hash}^1",
                    "date": ci["date"],
                    "subject": ci["subject"],
                    "files_changed": len(ci["files"]),
                    "file_list": ci["files"],
                    "insertions": ci["insertions"],
                    "deletions": ci["deletions"],
                    "total_lines": ci["total"],
                    "expected_diff": ci["diff"],
                    "description": ticket,
                    "classifier": "deepseek-v4-flash-free",
                }
                fname = f"{i:06d}_{ch_hash[:12]}.json"
                (TASKS_DIR / fname).write_text(json.dumps(test, indent=2))
                print(f"         -> {fname}", flush=True)

            state["last_idx"] = i
            if dec == "useful":
                state["useful"] = state.get("useful", 0) + 1
            else:
                state["useless"] = state.get("useless", 0) + 1

        # Save checkpoint after each API call
        CHECKPOINT.write_text(json.dumps(state))
        print(f"  State: useful={state.get('useful', 0)} useless={state.get('useless', 0)} last={state['last_idx']}", flush=True)

        start = end
        batch_i += 1
        time.sleep(args.delay)

    print(f"\nDone! Last index: {state.get('last_idx', -1)}", flush=True)


if __name__ == "__main__":
    main()
