#!/usr/bin/env python3
"""Simple benchmark: python3 bench.py [task_ids..]

Reads fastcontext creds from your ~/.config/opencode/opencode.json.
Runs all tasks (or specified IDs) through MCP + baseline, collects trajectory + tokens + diffs.
"""
import json, os, shutil, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).parent
LINUX_VOLUME = "linux-repo"

def load_user_fastcontext_config():
    """Read fastcontext MCP env vars from user's opencode config."""
    paths = [
        Path.home() / ".config/opencode/opencode.json",
        HERE.parent / "opencode.json",
    ]
    for p in paths:
        if p.exists():
            c = json.loads(p.read_text())
            mcp = c.get("mcp", {}).get("fastcontext", {})
            env = mcp.get("env", {})
            if env.get("API_KEY"):
                return env
    return {}

def build_image():
    subprocess.run(["docker","build","-t","fastcontext-test","-f",str(HERE/"Dockerfile"),str(HERE)], check=True)

def ensure_linux_volume():
    r = subprocess.run(["docker","volume","inspect",LINUX_VOLUME], capture_output=True)
    if r.returncode != 0:
        print("Creating linux-repo volume...")
        subprocess.run(["docker","volume","create",LINUX_VOLUME], check=True)
        host_repo = HERE.parent / "linux"
        if host_repo.exists():
            subprocess.run(["docker","run","--rm","-v",f"{LINUX_VOLUME}:/repo","-v",f"{host_repo}:/src:ro","alpine:3.19","sh","-c","cp -a /src/. /repo/"], check=True, capture_output=True)

def run_container(task_file, mode, results_dir, fc_cfg, timeout):
    results_dir.mkdir(parents=True, exist_ok=True)
    env = {"TASK_FILE":"/task.json","LINUX_REPO":"/linux-repo","CONFIG_DIR":"/config","RESULTS_DIR":"/results","MODEL":"opencode/deepseek-v4-flash-free","TIMEOUT":str(timeout)}
    if fc_cfg.get("MODEL"): env["FASTCONTEXT_MODEL"] = fc_cfg["MODEL"]
    if fc_cfg.get("BASE_URL"): env["FASTCONTEXT_BASE_URL"] = fc_cfg["BASE_URL"]
    if fc_cfg.get("API_KEY"): env["FASTCONTEXT_API_KEY"] = fc_cfg["API_KEY"]
    if fc_cfg.get("EXTRA_HEADERS"): env["FASTCONTEXT_EXTRA_HEADERS"] = fc_cfg["EXTRA_HEADERS"]

    eflags = sum([["-e",f"{k}={v}"] for k,v in env.items()], [])
    mounts = [
        "--mount",f"type=bind,source={task_file},target=/task.json,ro",
        "--mount",f"type=volume,source={LINUX_VOLUME},target=/linux-repo,ro",
        "--mount",f"type=bind,source={HERE/'inside'},target=/config,ro",
        "--mount",f"type=bind,source={HERE/'inside'/'run_single_test.sh'},target=/entrypoint.sh,ro",
        "--mount",f"type=bind,source={HERE.parent.parent},target=/fastcontext",
        "--mount",f"type=bind,source={results_dir},target=/results",
        "--mount","type=bind,source=/usr/bin/uv,target=/usr/local/bin/uv,ro",
        "--mount","type=bind,source=/usr/bin/opencode,target=/usr/local/bin/opencode,ro",
        "--mount","type=bind,source=/home/a/.local/share/opencode/auth.json,target=/root/.local/share/opencode/auth.json,ro",
    ]
    cmd = ["docker","run","--rm", *eflags, *mounts, "fastcontext-test", mode]

    start = time.time()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout+60)
        stdout = r.stdout
        if r.returncode != 0: print(f"  [{mode}] exit {r.returncode}", flush=True)
    except subprocess.TimeoutExpired:
        print(f"  [{mode}] TIMEOUT", flush=True); stdout = ""
    elapsed = time.time() - start

    tokens = json.loads((results_dir/"tokens.json").read_text()) if (results_dir/"tokens.json").exists() else {}
    diff = (results_dir/"changes.diff").read_text() if (results_dir/"changes.diff").exists() else ""
    traj = (results_dir/"trajectory.jsonl").stat().st_size if (results_dir/"trajectory.jsonl").exists() else 0
    return {"elapsed":round(elapsed,1),"has_diff":bool(diff),"tokens":tokens,"diff_size":len(diff),"trajectory_size":traj}

def run_verifier(task_file, mcp_diff, baseline_diff, expected_diff, output):
    cmd = [sys.executable, str(HERE/"verifier.py"), "--task", str(task_file), "--output", str(output)]
    for p, flag in [(mcp_diff,"--mcp-diff"),(baseline_diff,"--baseline-diff"),(expected_diff,"--expected-diff")]:
        if p and p.exists(): cmd += [flag, str(p)]
    subprocess.run(cmd, check=True)

def main():
    import argparse
    ap = argparse.ArgumentParser(description="FastContext Benchmark")
    ap.add_argument("task_ids", type=str, nargs="*", help="Task indices to run (default: all)")
    ap.add_argument("--no-build", action="store_true")
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--output", type=Path, default=HERE.parent/"results")
    ap.add_argument("--parallel", type=int, default=1)
    args = ap.parse_args()

    fc_cfg = load_user_fastcontext_config()
    if not fc_cfg.get("API_KEY"):
        print("ERROR: No fastcontext API key found in opencode config")
        sys.exit(1)

    if not args.no_build: build_image()
    ensure_linux_volume()
    args.output.mkdir(parents=True, exist_ok=True)

    task_files = sorted((HERE.parent/"tasks_llm").glob("*.json"))
    if args.task_ids:
        task_files = [t for t in task_files if any(t.name.startswith(f"{i:06d}_") for i in map(int, args.task_ids))]

    print(f"Benchmark: {len(task_files)} tasks, parallel={args.parallel}, timeout={args.timeout}s\n")
    summary = {"tasks":[], "timestamp":time.time()}

    def run_one(tf):
        task = json.loads(tf.read_text())
        tid = tf.stem; idx = int(tid.split("_")[0])
        print(f"\n{'='*60}\nTask {idx}: {task.get('subject','?')[:70]}\n{'='*60}", flush=True)
        tdir = args.output / tid; tdir.mkdir(parents=True, exist_ok=True)
        res = {"task_id":tid,"subject":task.get("subject","?"),"commit":task.get("commit","?"),"description":task.get("description","")}

        mr = {}
        with ThreadPoolExecutor(max_workers=2) as ex:
            fs = {ex.submit(run_container, tf, m, tdir/m, fc_cfg, args.timeout): m for m in ["mcp","baseline"]}
            for f in as_completed(fs):
                m = fs[f]
                try:
                    r = f.result(); mr[m] = r
                    print(f"  [{m}] {r['elapsed']}s, {r['tokens'].get('total_tokens',0)} tok, diff={r['diff_size']}b, traj={r['trajectory_size']}b", flush=True)
                except Exception as e: print(f"  [{m}] ERROR: {e}", flush=True); mr[m] = {}
        for m in ["mcp","baseline"]: res[m] = mr.get(m, {})

        print("  Verifying...", flush=True)
        vf = tdir/"verdict.json"
        try:
            run_verifier(tf, tdir/"mcp"/"changes.diff", tdir/"baseline"/"changes.diff", tdir/"mcp"/"expected.diff", vf)
            v = json.loads(vf.read_text()) if vf.exists() else {}
            res["verdict"] = {"ranking":v.get("ranking",[]),"best_match":v.get("best_match","?"),"scores":v.get("scores",{})}
            print(f"  Ranking: {res['verdict']['ranking']}  Best: {res['verdict']['best_match']}", flush=True)
        except Exception as e: print(f"  Verifier fail: {e}", flush=True); res["verdict"] = {"error":str(e)}

        (tdir/"summary.json").write_text(json.dumps(res, indent=2))
        return res

    if args.parallel > 1:
        with ThreadPoolExecutor(max_workers=args.parallel) as ex:
            fs = {ex.submit(run_one, tf): tf for tf in task_files}
            for f in as_completed(fs): summary["tasks"].append(f.result())
    else:
        for tf in task_files:
            try: summary["tasks"].append(run_one(tf))
            except Exception as e: print(f"Task {tf.stem} failed: {e}", flush=True); summary["tasks"].append({"task_id":tf.stem,"error":str(e)})

    (args.output/"summary.json").write_text(json.dumps(summary, indent=2))
    print(f"\n{'='*100}")
    print(f"{'Task':<15} {'MCP tok':<10} {'Base tok':<10} {'MCP time':<10} {'Base time':<10} {'Best':<10} {'Ranking':<35}")
    print(f"{'-'*100}")
    for t in summary["tasks"]:
        if "error" in t: print(f"{t['task_id'][:15]:<15} ERROR: {t['error']}"); continue
        m,b,v = t.get("mcp",{}), t.get("baseline",{}), t.get("verdict",{})
        print(f"{t['task_id'][:15]:<15} {m.get('tokens',{}).get('total_tokens',0):<10} {b.get('tokens',{}).get('total_tokens',0):<10} {m.get('elapsed',0):<10} {b.get('elapsed',0):<10} {v.get('best_match','?'):<10} {' > '.join(v.get('ranking',[])):<35}")

    mt = sum(t.get("mcp",{}).get("tokens",{}).get("total_tokens",0) for t in summary["tasks"])
    bt = sum(t.get("baseline",{}).get("tokens",{}).get("total_tokens",0) for t in summary["tasks"])
    print(f"\nTotal MCP: {mt}  Baseline: {bt}  Ratio: {mt/bt:.2f}x" if bt else "")
    print(f"Results: {args.output}/", flush=True)

if __name__ == "__main__": main()
