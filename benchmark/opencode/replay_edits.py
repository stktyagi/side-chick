#!/usr/bin/env python3
"""Replay edit tool calls from trajectory.jsonl to reconstruct changes.diff."""
import json, subprocess, tempfile, shutil, sys
from pathlib import Path
from collections import defaultdict

results = Path(__file__).resolve().parent / "results"
linux_repo = results.parent / "linux"
tasks_dir = results.parent / "tasks_llm"

for d in sorted(results.iterdir()):
    if not d.is_dir(): continue
    task_file = tasks_dir / f"{d.name}.json"
    if not task_file.exists() and '_' in d.name:
        task_file = tasks_dir / f"{d.name.split('_', 1)[1]}.json"
    if not task_file.exists():
        print(f"{d.name}: no task file")
        continue

    task = json.loads(task_file.read_text())
    parent = task.get("parent", "")
    if not parent:
        print(f"{d.name}: no parent commit")
        continue

    for mode in ["mcp", "baseline"]:
        traj_file = d / mode / "trajectory.jsonl"
        if not traj_file.exists(): continue

        edits = []
        with open(traj_file) as f:
            for line in f:
                line = line.strip()
                if not line: continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("type") != "tool_use":
                    continue
                part = ev.get("part", {})
                if part.get("tool") != "edit":
                    continue
                inp = part.get("state", {}).get("input", {})
                fp = inp.get("filePath", "")
                old = inp.get("oldString", "")
                new = inp.get("newString", "")
                if not fp:
                    continue
                edits.append((fp, old, new))

        if not edits:
            print(f"{d.name}/{mode}: no edits")
            continue

        # Normalize /tmp/tmp.XXX/path/to/file -> path/to/file
        norm = []
        for fp, old, new in edits:
            rel = fp
            idx = fp.find("/tmp/")
            if idx >= 0:
                rest = fp[idx + 5:]
                slash = rest.find("/")
                if slash >= 0:
                    rel = rest[slash + 1:]
            norm.append((rel, old, new))

        tmpdir = Path(tempfile.mkdtemp())
        try:
            file_edits = defaultdict(list)
            for rel, old, new in norm:
                file_edits[rel].append((old, new))

            for rel, edit_list in file_edits.items():
                r = subprocess.run(
                    ["git", "--git-dir", f"{linux_repo}/.git", "show", f"{parent}:{rel}"],
                    capture_output=True, text=True, timeout=30,
                )
                content = r.stdout if r.returncode == 0 else ""

                for old_str, new_str in edit_list:
                    if old_str:
                        cnt = content.count(old_str)
                        if cnt == 0:
                            print(f"  WARN: {d.name}/{mode}: oldString not found in {rel}")
                            continue
                        content = content.replace(old_str, new_str, 1)
                    else:
                        content = new_str + content

                mod = tmpdir / rel
                mod.parent.mkdir(parents=True, exist_ok=True)
                mod.write_text(content)

            diff_parts = []
            for rel in sorted(file_edits):
                r = subprocess.run(
                    ["git", "--git-dir", f"{linux_repo}/.git", "show", f"{parent}:{rel}"],
                    capture_output=True, text=True, timeout=30,
                )
                orig = r.stdout if r.returncode == 0 else ""
                mod = tmpdir / rel
                if not mod.exists():
                    continue
                mcontent = mod.read_text()
                if orig == mcontent:
                    continue

                of = tmpdir / ".orig" / rel
                of.parent.mkdir(parents=True, exist_ok=True)
                of.write_text(orig)

                r = subprocess.run(
                    ["diff", "-u", "--label", f"a/{rel}", "--label", f"b/{rel}",
                     str(of), str(mod)],
                    capture_output=True, text=True, timeout=30,
                )
                if r.stdout:
                    diff_parts.append(r.stdout)

            diff = "".join(diff_parts)
            (d / mode / "changes.diff").write_text(diff)
            stat = [l for l in diff.split("\n") if l.startswith("diff ")]
            (d / mode / "changes_stat.txt").write_text("\n".join(stat))
            print(f"{d.name}/{mode}: {len(norm)} edits, {len(file_edits)} files, {len(diff)}b diff")
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
