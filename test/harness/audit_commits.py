#!/usr/bin/env python3
"""Audit Linux kernel commits one-by-one. Reliable, resumable, with full logging.

Strategy:
- Process oldest to newest
- For each: read diff, classify, log decision
- For useful: generate test JSON with dev-ticket prompt
- Save state after EVERY commit (crash-safe)
- Resume from last checkpoint automatically
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
TEST_ROOT = HERE.parent

# State/checkpoint file
CHECKPOINT = TEST_ROOT / "audit_checkpoint.json"

# Output dirs
TASKS_DIR = TEST_ROOT / "tasks_audited"
LOGS_DIR = TEST_ROOT / "audit_logs"


def cmd(*a, cwd=None, timeout=30, check=True):
    r = subprocess.run(list(a), cwd=cwd, capture_output=True, text=True,
                       timeout=timeout, check=False)
    if check and r.returncode != 0:
        raise subprocess.CalledProcessError(r.returncode, list(a),
                                            output=r.stdout, stderr=r.stderr)
    return r.stdout.strip()


def has_parent(repo, commithash):
    out = cmd("git", "rev-parse", "-q", "--verify", f"{commithash}^1",
              cwd=repo, check=False)
    return bool(out)


# ── classification ─────────────────────────────────────────────

def classify(subject, files, ins, deletions):
    """Returns (decision, reason) where decision in {useful, maybe, useless}."""
    total = ins + deletions
    sl = subject.lower()
    ext_codes = {".c", ".h", ".S", ".s", ".asm"}
    c_files = [f for f in files if Path(f).suffix in ext_codes]

    # ── hard useless ──
    if subject.startswith(("Merge tag", "Merge branch")):
        return "useless", "merge"
    if subject.startswith("Revert"):
        return "useless", "revert"
    if subject.startswith("Linux "):
        return "useless", "version"

    if total > 500:
        return "useless", f"too large({total})"
    if total < 3:
        return "useless", f"too small({total})"
    if len(files) > 12:
        return "useless", f"too many files({len(files)})"

    # doc-only
    if files and all(Path(f).suffix not in ext_codes for f in files):
        only_bind = all("bindings" in f or f.endswith((".yaml", ".json"))
                        for f in files)
        only_dts = all(f.endswith((".dts", ".dtsi")) for f in files)
        only_kconfig = all(f.endswith("Kconfig") for f in files)
        only_doc = all(f.startswith("Documentation/") for f in files)
        only_test = all("selftest" in f or "kunit" in f for f in files)
        only_makefile = all("Makefile" in f for f in files)
        if only_bind:
            return "useless", "dt-bindings"
        if only_dts:
            return "useless", "dts"
        if only_kconfig:
            return "useless", "Kconfig"
        if only_doc:
            return "useless", "docs"
        if only_test:
            return "useless", "selftests"
        if only_makefile:
            return "useless", "Makefile"
        return "useless", "non-code"

    # trivial patterns
    trivial = [
        (r"fix.*typo", "typo"),
        (r"typo", "typo"),
        (r"fix.*warning", "warn-fix"),
        (r"fix.*comment", "comment"),
        (r"fix.*spelling", "spelling"),
        (r"fix (a|the) format", "formatting"),
        (r"^dt-bindings?:?\s", "dt-bindings"),
        (r"\.gitignore", "gitignore"),
        (r"cosmetic", "cosmetic"),
        (r"^MAINTAINERS:", "MAINTAINERS"),
        (r"update (copyright|license|spdx)", "license"),
        (r"remove unused", "removal"),
        (r"simplify with scoped for each", "mechanical"),
        (r"use scoped", "mechanical"),
        (r"convert to (bool|struct|helper|generic)", "mechanical"),
        (r"fix.*kernel-doc", "kernel-doc"),
        (r"kernel-doc", "kernel-doc"),
        (r"make.*static const", "static-const"),
        (r"const(.*attribute)?$", "const"),
        (r"^(\w+):\s*(\w+)\s+(correct|adjust|update|add|remove|drop|delete|clean|fix|rename|move|sort|group|reorder|align|tweak|change|use)\s", "generic"),
    ]
    for pat, rsn in trivial:
        if re.search(pat, sl):
            return "useless", rsn

    # quirk-only (ALSA / PCI quirk)
    if re.search(r"(quirk|device.id)", sl) and total < 5:
        return "useless", "quirk-only"

    # strong useful signals
    strong = [
        (r"fix.*(race|dead?lock|crash|corruption|null|use.after.free|oom|infinite|hang|bypass|vuln|sec|overflow|leak|regression|logic|error.path|refcount|wrong|invalid|double.free)", "bug-fix"),
        (r"(add|implement|support|enable|introduce) (a |the |new |initial )?(driver|support|check|validation|parameter|option|flag|interface|command|ioctl|handler|callback)", "feature"),
        (r"^.*:\s*(add|implement|introduce|enable) (support|for|the) ", "feature"),
        (r"rework", "rework"),
        (r"refactor", "refactor"),
        (r"prevent|avoid|correct", "bug-fix"),
        (r"workaround", "workaround"),
        (r"restore|re-instate", "restore"),
        (r"optimize|improve performance", "optimization"),
    ]
    for pat, rsn in strong:
        if re.search(pat, sl):
            return "useful", rsn

    # medium signal → maybe if enough C lines
    if c_files and total >= 5:
        return "maybe", f"C-change({len(c_files)}f/{total}l)"
    return "useless", f"default({len(files)}f/{total}l)"


# ── prompt generation ──────────────────────────────────────────

def build_prompt(subject, body):
    if ":" in subject:
        subsys = subject.split(":")[0].strip()
        desc = subject.split(":", 1)[1].strip()
    else:
        subsys = "kernel"
        desc = subject

    dl = desc.lower()

    if any(w in dl for w in ["fix", "bug", "avoid", "prevent", "correct",
                              "workaround", "race", "crash", "leak", "deadlock"]):
        ttype = "Bug Fix"
    elif any(w in dl for w in ["add", "support", "implement", "introduce", "enable"]):
        ttype = "Feature Implementation"
    elif any(w in dl for w in ["refactor", "rework"]):
        ttype = "Refactoring"
    elif any(w in dl for w in ["optimize", "improve", "performance"]):
        ttype = "Optimization"
    else:
        ttype = "Code Change"

    # clean body
    body_lines = []
    for line in body.split("\n"):
        if re.match(r"^(Signed-off-by|Reported-by|Reviewed-by|Tested-by|"
                     r"Suggested-by|Acked-by|Cc:|Link:|Closes:|Fixes:|"
                     r"Co-developed-by|Based-on-patch-by):", line):
            continue
        body_lines.append(line)
    body_clean = "\n".join(body_lines).strip()
    if body_clean == desc or body_clean == subject:
        body_clean = ""

    prompt = f"""# {ttype}: {subject}

## Description
The Linux kernel {subsys} subsystem needs the following change:

{desc}"""

    if body_clean:
        prompt += f"""

{body_clean}"""

    prompt += """

## Requirements
1. Implement the necessary changes to achieve the described behavior
2. Follow Linux kernel coding style and conventions
3. Ensure proper error handling and edge cases
4. Add kernel-doc comments for any new functions or structures
5. Compile with no new warnings

## Acceptance Criteria
- The change compiles without errors or warnings for the relevant subsystem
- All existing functionality is preserved
- The implementation follows established patterns in the surrounding code
- Proper locking and memory management are maintained
- Any new interfaces are properly documented

## Notes
- Study the surrounding code carefully to understand existing patterns
- Make minimal changes — only what is needed
- Pay attention to subsystem-specific conventions
- Consider all edge cases and error paths"""

    if ttype == "Bug Fix":
        prompt += """
- Identify the root cause of the issue
- Apply the minimal fix, with comments explaining why it is correct
- Check if similar issues exist elsewhere in the same file or subsystem"""
    elif ttype == "Feature Implementation":
        prompt += """
- Study existing similar features/drivers in the subsystem for patterns
- Ensure proper integration with the surrounding infrastructure
- Add appropriate conditional compilation if hardware-dependent"""

    return prompt


# ── main processor ─────────────────────────────────────────────

def process_commit(repo, commithash, index):
    # Check parent
    if not has_parent(repo, commithash):
        return {
            "index": index, "hash": commithash,
            "decision": "useless", "reason": "shallow-boundary",
        }

    # Basic info
    info = cmd("git", "log", "--format=%H%n%ai%n%s%n%an%n%ae", "-1",
               commithash, cwd=repo)
    parts = info.split("\n")
    if len(parts) < 5:
        return {
            "index": index, "hash": commithash,
            "decision": "useless", "reason": "parse-error",
        }
    subject = parts[2]

    # Diff stat
    stat = cmd("git", "diff", "--stat", "--no-color",
               f"{commithash}^1..{commithash}", cwd=repo, check=False)
    files = []
    ins = dels = 0
    if stat:
        for line in stat.split("\n")[:-1]:
            p = line.split("|")
            if len(p) >= 2:
                files.append(p[0].strip())
        last = stat.split("\n")[-1]
        m_i = re.search(r"(\d+) insertion", last)
        m_d = re.search(r"(\d+) deletion", last)
        if m_i: ins = int(m_i.group(1))
        if m_d: dels = int(m_d.group(1))

    decision, reason = classify(subject, files, ins, dels)
    total = ins + dels

    result = {
        "index": index,
        "hash": commithash,
        "date": parts[1],
        "subject": subject,
        "author": parts[3],
        "decision": decision,
        "reason": reason,
        "files": len(files),
        "total_lines": total,
    }

    if decision in ("useful", "maybe"):
        # Fetch full data
        diff = cmd("git", "diff", "--no-color", f"{commithash}^1..{commithash}",
                   cwd=repo, check=False) or ""
        body = cmd("git", "log", "--format=%b", "-1", commithash, cwd=repo,
                   check=False) or ""
        prompt = build_prompt(subject, body)
        result["_test"] = {
            "commit": commithash,
            "date": parts[1],
            "subject": subject,
            "files_changed": files,
            "insertions": ins,
            "deletions": dels,
            "total_lines": total,
            "expected_diff": diff,
            "prompt": prompt,
            "classifier": "audit-script",
            "decision": decision,
            "reason": reason,
        }

    return result


def save_state(state):
    CHECKPOINT.parent.mkdir(parents=True, exist_ok=True)
    with open(CHECKPOINT, "w") as f:
        json.dump(state, f, indent=2)


def load_state():
    if CHECKPOINT.exists():
        try:
            with open(CHECKPOINT) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def main():
    ap = argparse.ArgumentParser(description="Audit Linux kernel commits")
    ap.add_argument("--repo", type=Path, required=True, help="Linux repo")
    ap.add_argument("--batch", type=int, default=200, help="Batch size")
    ap.add_argument("--resume", action="store_true", default=True)
    ap.add_argument("--fresh", action="store_true", help="Start over")
    ap.add_argument("--include-maybe", action="store_true",
                    help="Include 'maybe' commits as useful")
    args = ap.parse_args()

    repo = args.repo
    TASKS_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    # Get all commits
    print("Loading commit list...", flush=True)
    all_commits = cmd("git", "log", "--format=%H", "--reverse", cwd=repo).split("\n")
    total = len(all_commits)
    print(f"Total: {total} commits", flush=True)

    # Load/resume state
    state = {"last_idx": -1, "useful": 0, "maybe": 0, "useless": 0,
             "processed": 0} if args.fresh else load_state()
    if not state:
        state = {"last_idx": -1, "useful": 0, "maybe": 0, "useless": 0,
                 "processed": 0}

    start = state.get("last_idx", -1) + 1
    end = min(start + args.batch, total)
    if start >= total:
        print("All commits processed.", flush=True)
        return

    print(f"Processing {start}→{end-1} (batch {args.batch})", flush=True)

    log_file = LOGS_DIR / f"audit_{start:06d}_{end-1:06d}.txt"
    with open(log_file, "w") as lf:
        lf.write(f"# audit batch {start}-{end-1}  {time.ctime()}\n")

        for i in range(start, end):
            ch = all_commits[i]
            pct = (i - start + 1) / args.batch * 100

            try:
                r = process_commit(repo, ch, i)
            except Exception as e:
                r = {"index": i, "hash": ch, "decision": "error",
                     "reason": str(e), "files": 0, "total_lines": 0}

            decision = r["decision"]
            reason = r["reason"]
            subj = r.get("subject", "?")[:80]

            # Update counts
            if decision == "useful":
                state["useful"] = state.get("useful", 0) + 1
            elif decision == "maybe":
                state["maybe"] = state.get("maybe", 0) + 1
            else:
                state["useless"] = state.get("useless", 0) + 1

            state["last_idx"] = i
            state["processed"] = state.get("processed", 0) + 1

            # Log
            tag = "USEFUL" if decision == "useful" else \
                  "MAYBE " if decision == "maybe" else "  -   "
            line = f"[{tag}] {i:05d} {ch[:12]} {reason:20s} {subj}"
            lf.write(line + "\n")
            lf.flush()

            # Write test for useful/maybe
            do_test = (decision == "useful" or
                       (decision == "maybe" and args.include_maybe))
            if do_test and "_test" in r:
                fname = f"{i:06d}_{ch[:12]}.json"
                with open(TASKS_DIR / fname, "w") as tf:
                    json.dump(r["_test"], tf, indent=2)
                lf.write(f"    → test: {fname}\n")
                lf.flush()

            # Progress indicator
            if (i - start + 1) % 50 == 0:
                u = state.get("useful", 0)
                m = state.get("maybe", 0)
                us = state.get("useless", 0)
                ttl = u + m + us
                print(f"  {i-start+1}/{args.batch} | "
                      f"useful={u} maybe={m} useless={us} total={ttl}",
                      flush=True)

            # Save state every 10 commits
            if (i - start + 1) % 10 == 0:
                save_state(state)

    save_state(state)

    u = state.get("useful", 0)
    m = state.get("maybe", 0)
    us = state.get("useless", 0)
    print(f"\nBatch done. Total: useful={u} maybe={m} useless={us} "
          f"| last_idx={state['last_idx']} | next start={state['last_idx']+1}",
          flush=True)


if __name__ == "__main__":
    main()