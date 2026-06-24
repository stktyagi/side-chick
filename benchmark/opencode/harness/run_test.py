#!/usr/bin/env python3
"""Orchestrate benchmark: run MCP & baseline on tasks, collect tokens, verify.

Usage:
    python3 run_test.py --tasks-dir test/tasks_llm/ [--no-build]
    
Source is prepared INSIDE the container (tmpfs) — no host-side work dir or repo.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


HERE = Path(__file__).parent
REPO_ROOT = HERE.parent.parent


def build_image():
    print("=== Building Docker image ===")
    subprocess.run(
        ["docker", "build", "-t", "aide-test", "-f", str(HERE / "Dockerfile"), str(HERE)],
        check=True,
    )


LINUX_REPO_VOLUME = "linux-repo"


def ensure_linux_repo_volume():
    """Create and populate linux-repo Docker volume if it doesn't exist."""
    result = subprocess.run(
        ["docker", "volume", "inspect", LINUX_REPO_VOLUME],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print("=== Creating linux-repo Docker volume ===")
        subprocess.run(["docker", "volume", "create", LINUX_REPO_VOLUME], check=True)
        print("Copying linux repo into volume (one-time)...")
        # The host path is only used for this one-time seeding
        host_repo = HERE.parent / "linux"
        if host_repo.exists():
            subprocess.run(
                ["docker", "run", "--rm",
                 "-v", f"{LINUX_REPO_VOLUME}:/repo",
                 "-v", f"{host_repo}:/src:ro",
                 "alpine:3.19", "sh", "-c", "cp -a /src/. /repo/"],
                check=True, capture_output=True,
            )
            print("  Done.")
        else:
            print("  WARNING: No linux repo found at", host_repo)
    return LINUX_REPO_VOLUME


def run_container(task_file: Path, mode: str, results_dir: Path,
                  model: str, aide_model: str, aide_base_url: str,
                  aide_api_key: str, aide_headers: str | None,
                  timeout: int) -> dict:
    """Run one container for one mode. Source prep done inside container."""
    results_dir.mkdir(parents=True, exist_ok=True)

    env = {
        "TASK_FILE": "/task.json",
        "LINUX_REPO": "/linux-repo",
        "CONFIG_DIR": "/config",
        "RESULTS_DIR": "/results",
        "MODEL": model,
        "TIMEOUT": str(timeout),
        "AIDE_MODEL": aide_model,
        "AIDE_BASE_URL": aide_base_url,
        "AIDE_API_KEY": aide_api_key,
    }
    if aide_headers:
        env["AIDE_EXTRA_HEADERS"] = aide_headers

    env_flags = []
    for k, v in env.items():
        env_flags.extend(["-e", f"{k}={v}"])

    mounts = [
        "--mount", f"type=bind,source={task_file},target=/task.json,ro",
        "--mount", f"type=volume,source={LINUX_REPO_VOLUME},target=/linux-repo,ro",
        "--mount", f"type=bind,source={HERE / 'inside'},target=/config,ro",
        "--mount", f"type=bind,source={HERE / 'inside' / 'run_single_test.sh'},target=/entrypoint.sh,ro",
        "--mount", f"type=bind,source={REPO_ROOT},target=/aide",
        "--mount", f"type=bind,source={results_dir},target=/results",
        "--mount", "type=bind,source=/usr/bin/uv,target=/usr/local/bin/uv,ro",
        "--mount", "type=bind,source=/usr/bin/opencode,target=/usr/local/bin/opencode,ro",
        "--mount", "type=bind,source=/home/a/.local/share/opencode/auth.json,target=/root/.local/share/opencode/auth.json,ro",
    ]

    cmd = [
        "docker", "run", "--rm",
        *env_flags,
        *mounts,
        "aide-test",
        mode,
    ]

    start = time.time()
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 60)
        stdout = result.stdout
        if result.returncode != 0:
            print(f"  [{mode}] WARN: exit code {result.returncode}", flush=True)
            if result.stderr:
                print(f"  [{mode}] stderr: {result.stderr[-500:]}", flush=True)
    except subprocess.TimeoutExpired:
        print(f"  [{mode}] TIMEOUT after {timeout}s", flush=True)
        stdout = ""
    elapsed = time.time() - start

    # Load results
    tokens = {}
    tf = results_dir / "tokens.json"
    if tf.exists():
        tokens = json.loads(tf.read_text())

    changes_file = results_dir / "changes.diff"
    diff = changes_file.read_text() if changes_file.exists() else ""
    trajectory_file = results_dir / "trajectory.jsonl"
    trajectory_size = trajectory_file.stat().st_size if trajectory_file.exists() else 0
    expected_file = results_dir / "expected.diff"
    expected = expected_file.read_text() if expected_file.exists() else ""

    return {
        "elapsed": round(elapsed, 1),
        "has_diff": changes_file.exists() and changes_file.stat().st_size > 0,
        "tokens": tokens,
        "diff_size": len(diff),
        "trajectory_size": trajectory_size,
        "expected_size": len(expected),
        "stdout_tail": stdout[-1000:] if stdout else "",
    }


def run_verifier(task_file: Path, mcp_diff: Path | None, baseline_diff: Path | None,
                 expected_diff: Path | None, output: Path):
    cmd = [
        sys.executable, str(HERE / "verifier.py"),
        "--task", str(task_file),
        "--output", str(output),
    ]
    if mcp_diff and mcp_diff.exists():
        cmd += ["--mcp-diff", str(mcp_diff)]
    if baseline_diff and baseline_diff.exists():
        cmd += ["--baseline-diff", str(baseline_diff)]
    if expected_diff and expected_diff.exists():
        cmd += ["--expected-diff", str(expected_diff)]
    subprocess.run(cmd, check=True)


def run_single_task(task_file: Path, args) -> dict:
    """Run both modes for one task, return result dict."""
    task = json.loads(task_file.read_text())
    task_id = task_file.stem
    task_idx = int(task_id.split("_")[0])

    print(f"\n{'='*60}", flush=True)
    print(f"Task {task_idx}: {task.get('subject', '?')[:80]}", flush=True)
    print(f"{'='*60}", flush=True)

    # Task result dir: results/{task_id}/
    task_result_dir = args.output_dir / task_id
    task_result_dir.mkdir(parents=True, exist_ok=True)

    task_result = {
        "task_id": task_id,
        "subject": task.get("subject", "?"),
        "commit": task.get("commit", "?"),
        "description": task.get("description", ""),
    }

    # Run MCP and baseline in parallel
    print("--- Running MCP + baseline in parallel ---", flush=True)
    mode_results = {}
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {}
        for mode in ["mcp", "baseline"]:
            mode_dir = task_result_dir / mode
            future = executor.submit(
                run_container,
                task_file=task_file,
                mode=mode,
                results_dir=mode_dir,
                model=args.model,
                aide_model=args.aide_model,
                aide_base_url=args.aide_base_url,
                aide_api_key=args.aide_api_key,
                aide_headers=args.aide_headers,
                timeout=args.timeout,
            )
            futures[future] = mode

        for future in as_completed(futures):
            mode = futures[future]
            try:
                result = future.result()
                mode_results[mode] = result
                print(f"  [{mode}] done: {result['elapsed']}s, "
                      f"{result['tokens'].get('total_tokens', 0)} tokens, "
                      f"diff={result['diff_size']}b, "
                      f"trajectory={result['trajectory_size']}b", flush=True)
            except Exception as e:
                print(f"  [{mode}] ERROR: {e}", flush=True)
                mode_results[mode] = {
                    "elapsed": 0, "has_diff": False, "tokens": {},
                    "diff_size": 0, "trajectory_size": 0, "stdout_tail": str(e),
                }

    for mode in ["mcp", "baseline"]:
        task_result[mode] = mode_results.get(mode, {
            "elapsed": 0, "has_diff": False, "tokens": {}, "diff_size": 0,
        })

    # Print per-mode stdout tails
    for mode in ["mcp", "baseline"]:
        tail = mode_results.get(mode, {}).get("stdout_tail", "")
        if tail:
            print(f"\n  [{mode}] last output:", flush=True)
            for line in tail.strip().split("\n")[-10:]:
                print(f"    {line}", flush=True)

    # Run verifier
    print("--- Verifying ---", flush=True)
    verdict_file = task_result_dir / "verdict.json"
    try:
        run_verifier(
            task_file=task_file,
            mcp_diff=task_result_dir / "mcp" / "changes.diff",
            baseline_diff=task_result_dir / "baseline" / "changes.diff",
            expected_diff=task_result_dir / "mcp" / "expected.diff",
            output=verdict_file,
        )
        verdict = json.loads(verdict_file.read_text()) if verdict_file.exists() else {}
        task_result["verdict"] = {
            "ranking": verdict.get("ranking", []),
            "best_match": verdict.get("best_match", "?"),
            "scores": verdict.get("scores", {}),
        }
        print(f"  Ranking: {task_result['verdict']['ranking']}", flush=True)
        print(f"  Best: {task_result['verdict']['best_match']}", flush=True)
    except Exception as e:
        print(f"  Verifier failed: {e}", flush=True)
        task_result["verdict"] = {"error": str(e)}

    # Save per-task summary
    (task_result_dir / "summary.json").write_text(json.dumps(task_result, indent=2))

    return task_result


def main():
    ap = argparse.ArgumentParser(description="Run aide benchmark harness")
    ap.add_argument("--tasks-dir", type=Path, default=HERE.parent / "tasks_llm",
                    help="Directory with task JSONs")
    ap.add_argument("--task-ids", type=str, nargs="*",
                    help="Specific task indices to run (default: all)")
    ap.add_argument("--no-build", action="store_true")
    ap.add_argument("--model", default="opencode/deepseek-v4-flash-free")
    ap.add_argument("--aide-model", default=os.getenv("MODEL", "qwen/qwen3.6-27b"))
    ap.add_argument("--aide-base-url",
                    default=os.getenv("BASE_URL", "https://api.groq.com/openai/v1"))
    ap.add_argument("--aide-api-key", default=os.getenv("API_KEY", ""))
    ap.add_argument("--aide-headers", default=os.getenv("EXTRA_HEADERS"))
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--output-dir", type=Path, default=HERE.parent / "results")
    ap.add_argument("--parallel-tasks", type=int, default=1,
                    help="Number of tasks to run in parallel (default: 1)")
    args = ap.parse_args()

    if not args.no_build:
        build_image()

    # Ensure linux-repo Docker volume exists
    ensure_linux_repo_volume()

    args.output_dir.mkdir(parents=True, exist_ok=True)

    # Load tasks
    task_files = sorted(args.tasks_dir.glob("*.json"))
    if args.task_ids:
        task_files = [t for t in task_files
                      if any(t.name.startswith(f"{i:06d}_") for i in map(int, args.task_ids))]

    print(f"Running {len(task_files)} tasks (parallel: {args.parallel_tasks})\n", flush=True)

    summary = {"tasks": [], "timestamp": time.time()}

    # Run tasks
    if args.parallel_tasks > 1:
        with ThreadPoolExecutor(max_workers=args.parallel_tasks) as executor:
            futures = {
                executor.submit(run_single_task, tf, args): tf
                for tf in task_files
            }
            for future in as_completed(futures):
                tf = futures[future]
                try:
                    result = future.result()
                    summary["tasks"].append(result)
                except Exception as e:
                    print(f"Task {tf.stem} failed: {e}", flush=True)
                    summary["tasks"].append({"task_id": tf.stem, "error": str(e)})
    else:
        for tf in task_files:
            try:
                result = run_single_task(tf, args)
                summary["tasks"].append(result)
            except Exception as e:
                print(f"Task {tf.stem} failed: {e}", flush=True)
                summary["tasks"].append({"task_id": tf.stem, "error": str(e)})

    # Final summary
    summary_file = args.output_dir / "summary.json"
    summary_file.write_text(json.dumps(summary, indent=2))
    print(f"\nSummary saved to {summary_file}", flush=True)

    # Print table
    print(f"\n{'='*100}", flush=True)
    h = f"{'Task':<15} {'MCP tok':<10} {'Base tok':<10} {'MCP time':<10} {'Base time':<10} {'MCP diff':<10} {'Base diff':<10} {'Best':<10} {'Ranking':<30}"
    print(h, flush=True)
    print(f"{'-'*100}", flush=True)
    for t in summary["tasks"]:
        if "error" in t:
            print(f"{t['task_id'][:15]:<15} ERROR: {t['error']}", flush=True)
            continue
        m = t.get("mcp", {})
        b = t.get("baseline", {})
        v = t.get("verdict", {})
        tid = t["task_id"][:15]
        mtok = m.get("tokens", {}).get("total_tokens", 0)
        btok = b.get("tokens", {}).get("total_tokens", 0)
        mtim = m.get("elapsed", 0)
        btim = b.get("elapsed", 0)
        mdiff = m.get("diff_size", 0)
        bdiff = b.get("diff_size", 0)
        best = v.get("best_match", "?")
        rank = " > ".join(v.get("ranking", []))
        print(f"{tid:<15} {mtok:<10} {btok:<10} {mtim:<10} {btim:<10} {mdiff:<10} {bdiff:<10} {best:<10} {rank:<30}", flush=True)

    # Aggregate token comparison
    mcp_total = sum(t.get("mcp", {}).get("tokens", {}).get("total_tokens", 0) for t in summary["tasks"])
    base_total = sum(t.get("baseline", {}).get("tokens", {}).get("total_tokens", 0) for t in summary["tasks"])
    print(f"\n{'='*100}", flush=True)
    print(f"Total MCP tokens: {mcp_total}", flush=True)
    print(f"Total Baseline tokens: {base_total}", flush=True)
    if base_total > 0:
        ratio = mcp_total / base_total
        print(f"MCP/Baseline ratio: {ratio:.2f}x", flush=True)

    print(f"\nResults in: {args.output_dir}", flush=True)
    print(f"  {{task_id}}/{{mcp|baseline}}/trajectory.jsonl  (full JSON log)", flush=True)
    print(f"  {{task_id}}/{{mcp|baseline}}/changes.diff      (produced diff)", flush=True)
    print(f"  {{task_id}}/{{mcp|baseline}}/expected.diff     (ground truth)", flush=True)
    print(f"  {{task_id}}/verdict.json                       (verifier ranking)", flush=True)
    print(f"  {{task_id}}/summary.json                       (per-task summary)", flush=True)


if __name__ == "__main__":
    main()
