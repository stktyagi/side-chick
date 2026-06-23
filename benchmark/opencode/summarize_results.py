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
    subject = s.get("subject", "?")[:55]

    mcp = s.get("mcp", {}) or {}
    bl = s.get("baseline", {}) or {}

    mcp_time = mcp.get("elapsed", -1)
    bl_time = bl.get("elapsed", -1)
    mcp_tok = (mcp.get("tokens") or {}).get("total_tokens", 0)
    bl_tok = (bl.get("tokens") or {}).get("total_tokens", 0)
    mcp_diff = mcp.get("diff_size", -1)
    bl_diff = bl.get("diff_size", -1)
    mcp_traj = mcp.get("trajectory_size", -1)
    bl_traj = bl.get("trajectory_size", -1)

    ranking = v.get("ranking", [])
    best_match = v.get("best_match", "?")
    ranking_raw = v.get("ranking", [])
    # if expected is top, show second-best (A/B) as top
    display_best = ranking_raw[1] if (len(ranking_raw) >= 2 and ranking_raw[0] == "expected") else best_match
    scores = v.get("scores", {})

    rows.append({
        "id": task_id,
        "subject": subject,
        "mcp_time": mcp_time,
        "bl_time": bl_time,
        "mcp_tok": mcp_tok,
        "bl_tok": bl_tok,
        "mcp_diff": mcp_diff,
        "bl_diff": bl_diff,
        "mcp_traj": mcp_traj,
        "bl_traj": bl_traj,
        "best": display_best,
        "ranking": " > ".join(ranking_raw) if ranking_raw else "?",
        "scores": scores,
    })

# --- TABLE ---
print(f"{'Task ID':22s} {'MCP(s)':>8s} {'BL(s)':>8s} {'MCPtok':>7s} {'BLtok':>7s} {'Top':>5s}  Subject")
print("="*110)
for r in rows:
    mt = f"{r['mcp_time']:7.1f}" if r['mcp_time'] >= 0 else "   TIMEOUT"
    bt = f"{r['bl_time']:7.1f}" if r['bl_time'] >= 0 else "   TIMEOUT"
    print(f"{r['id']:22s} {mt:>8s} {bt:>8s} {r['mcp_tok']:7d} {r['bl_tok']:7d} {r['best']:>5s}  {r['subject']}")

# --- AVERAGES ROW ---
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

print(f"{'─'*110}")
print(f"{'AVERAGE':22s} {avg_mcp_t:8.1f} {avg_bl_t:8.1f} {avg_mcp_tok:7.0f} {avg_bl_tok:7.0f}")

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