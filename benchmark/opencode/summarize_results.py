#!/usr/bin/env python3
"""Summarize all completed benchmark results."""
import json, sys
from pathlib import Path
from collections import Counter

RESULTS = Path(__file__).resolve().parent / "results"

rows = []
for d in sorted(RESULTS.iterdir()):
    if not d.is_dir(): continue
    sfile = d / "summary.json"
    vfile = d / "verdict.json"
    if not sfile.exists(): continue
    s = json.loads(sfile.read_text())
    v = json.loads(vfile.read_text()) if vfile.exists() else {}

    task_id = s.get("task_id", d.name)
    # Short numeric ID: extract leading digits
    tid_short = task_id.split("_")[0].lstrip("0") or "0"
    subject = s.get("subject", "?")[:40]

    mcp = s.get("mcp", {}) or {}
    bl = s.get("baseline", {}) or {}

    mcp_time = mcp.get("elapsed", -1)
    bl_time = bl.get("elapsed", -1)
    mcp_tok = (mcp.get("tokens") or {}).get("total_tokens", 0)
    bl_tok = (bl.get("tokens") or {}).get("total_tokens", 0)

    # Read actual diff sizes from changes.diff files
    mcp_diff_file = d / "mcp" / "changes.diff"
    bl_diff_file = d / "baseline" / "changes.diff"
    mcp_diff = len(mcp_diff_file.read_text()) if mcp_diff_file.exists() else -1
    bl_diff = len(bl_diff_file.read_text()) if bl_diff_file.exists() else -1

    # Count tool calls from trajectory
    def count_tools(traj_path):
        """Return (reads, edits_writes) counts."""
        if not traj_path.exists(): return 0, 0
        r = e = 0
        with open(traj_path) as f:
            for line in f:
                line = line.strip()
                if not line: continue
                try: ev = json.loads(line)
                except: continue
                if ev.get("type") != "tool_use": continue
                tool = ev.get("part", {}).get("tool", "")
                if tool == "read": r += 1
                elif tool in ("edit", "write"): e += 1
        return r, e
    mcp_reads, mcp_edits = count_tools(d / "mcp" / "trajectory.jsonl")
    bl_reads, bl_edits = count_tools(d / "baseline" / "trajectory.jsonl")

    ranking = v.get("ranking", [])
    best_match = v.get("best_match", "?")
    ranking_raw = v.get("ranking", [])
    # if expected is top, show second-best (A/B) as top
    display_best = ranking_raw[1] if (len(ranking_raw) >= 2 and ranking_raw[0] == "expected") else best_match
    scores = v.get("scores", {})

    rows.append({
        "id": tid_short,
        "subject": subject,
        "mcp_time": mcp_time,
        "bl_time": bl_time,
        "mcp_tok": mcp_tok,
        "bl_tok": bl_tok,
        "mcp_diff": mcp_diff,
        "bl_diff": bl_diff,
        "mcp_reads": mcp_reads,
        "bl_reads": bl_reads,
        "mcp_edits": mcp_edits,
        "bl_edits": bl_edits,
        "best": display_best,
        "ranking": " > ".join(ranking_raw) if ranking_raw else "?",
        "scores": scores,
    })

# --- TABLE with box-drawing ---
col = {
    "id": 4,
    "mcp": 29,   # merged: " 151.6   35752   352  12/ 1"
    "bl": 29,    # merged: " 264.4   53654  1533  45/ 7"
    "top": 3,
}

def sep(left, mid, right, *widths):
    parts = [left]
    for i, w in enumerate(widths):
        parts.append("─" * w)
        if i < len(widths) - 1:
            parts.append(mid)
    parts.append(right)
    return "".join(parts)

W = [col["id"], col["mcp"], col["bl"], col["top"]]

# Top border
print(sep("┌", "┬", "┐", *W))

# Header row 1: group labels
h1 = f"│{'':>{col['id']}}│{' MCP':^{col['mcp']}}│{' BL':^{col['bl']}}│{'':>{col['top']}}│"
print(h1)

# Separator
print(sep("├", "┼", "┤", *W))

# Header row 2: sub-labels right-aligned in their column widths
# time(7) + space(1) + tok(7) + space(1) + diff(5) + space(1) + r(3) + space(1) + w(3) = 29
h2_sub = f"{'time':>7s} {'tok':>7s} {'diff':>5s} {'r':>3s} {'w':>3s}"
h2 = f"│{' #':>{col['id']}}│{h2_sub:^{col['mcp']}}│{h2_sub:^{col['bl']}}│{'Top':>{col['top']}}│"
print(h2)

# Separator under headers
print(sep("├", "┼", "┤", *W))

# Data rows (no separators between rows)
for r in rows:
    mt = f"{r['mcp_time']:>7.1f}" if r['mcp_time'] >= 0 else "  TIMEOUT"
    bt = f"{r['bl_time']:>7.1f}" if r['bl_time'] >= 0 else "  TIMEOUT"
    md = f"{r['mcp_diff']:>5d}" if r['mcp_diff'] >= 0 else "    ?"
    bd = f"{r['bl_diff']:>5d}" if r['bl_diff'] >= 0 else "    ?"
    mr = f"{r['mcp_reads']:>3d}"
    br = f"{r['bl_reads']:>3d}"
    me = f"{r['mcp_edits']:>3d}"
    be = f"{r['bl_edits']:>3d}"
    # Build merged cell: " 151.6   35752   352   6   1"
    mcp_cell = f"{mt} {r['mcp_tok']:>7d} {md} {mr} {me}"
    bl_cell = f"{bt} {r['bl_tok']:>7d} {bd} {br} {be}"
    print(f"│{r['id']:>{col['id']}}│{mcp_cell:<{col['mcp']}}│{bl_cell:<{col['bl']}}│{r['best']:>{col['top']}}│")

# Bottom border
print(sep("└", "┴", "┘", *W))

# --- AVERAGES ---
def avg(vals):
    f = [v for v in vals if v >= 0]
    return sum(f)/len(f) if f else 0

def avg_tok(vals):
    f = [v for v in vals if v > 0]
    return sum(f)/len(f) if f else 0

avg_mcp_t = avg([r['mcp_time'] for r in rows])
avg_bl_t = avg([r['bl_time'] for r in rows])
avg_mcp_tok = avg_tok([r['mcp_tok'] for r in rows if r['mcp_time'] >= 0])
avg_bl_tok = avg_tok([r['bl_tok'] for r in rows if r['bl_time'] >= 0])
avg_mcp_diff = avg([r['mcp_diff'] for r in rows if r['mcp_diff'] >= 0])
avg_bl_diff = avg([r['bl_diff'] for r in rows if r['bl_diff'] >= 0])
avg_mcp_edits = avg([r['mcp_edits'] for r in rows])
avg_bl_edits = avg([r['bl_edits'] for r in rows])

# AVG line
avg_mt = f"{avg_mcp_t:>7.1f}"
avg_bt = f"{avg_bl_t:>7.1f}"
avg_md = f"{avg_mcp_diff:>5.0f}"
avg_bd = f"{avg_bl_diff:>5.0f}"
avg_mr = f"{avg([r['mcp_reads'] for r in rows]):>3.0f}"
avg_br = f"{avg([r['bl_reads'] for r in rows]):>3.0f}"
avg_me = f"{avg_mcp_edits:>3.0f}"
avg_be = f"{avg_bl_edits:>3.0f}"
avg_mcp_cell = f"{avg_mt} {avg_mcp_tok:>7.0f} {avg_md} {avg_mr} {avg_me}"
avg_bl_cell = f"{avg_bt} {avg_bl_tok:>7.0f} {avg_bd} {avg_br} {avg_be}"
print(f"│{'AVG':>{col['id']}}│{avg_mcp_cell:<{col['mcp']}}│{avg_bl_cell:<{col['bl']}}│{'':>{col['top']}}│")

# --- SUMMARY STATS ---
completed_mcp = [r for r in rows if r['mcp_time'] >= 0]
completed_bl = [r for r in rows if r['bl_time'] >= 0]
timedout_mcp = [r for r in rows if r['mcp_time'] < 0]
timedout_bl = [r for r in rows if r['bl_time'] < 0]
verdicts = Counter(r['best'] for r in rows if r['best'] != "?")

print(f"\n{'─'*80}")
print(f"Total: {len(rows)} tasks")
print(f"MCP completed: {len(completed_mcp)}  timed out: {len(timedout_mcp)}")
print(f"Baseline completed: {len(completed_bl)}  timed out: {len(timedout_bl)}")

print("\nVerdicts (best match):")
for k in sorted(verdicts):
    print(f"  {k}: {verdicts[k]}")