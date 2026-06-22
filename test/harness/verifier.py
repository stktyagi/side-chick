#!/usr/bin/env python3
"""Verifier: ranks MCP, baseline, and expected solutions by quality.

Usage:
    python3 verifier.py --task task.json --mcp-diff mcp.diff --baseline-diff baseline.diff --expected-diff expected.diff
"""

import argparse
import json
import os
import re
import urllib.request
import urllib.error
from pathlib import Path


API_URL = "https://opencode.ai/zen/v1/chat/completions"
API_KEY = "sk-6JSmAIjfLH1qzFLCwYmEEmY02owIjyUnVIFkNPFAcPvS6MCv61EAJLkl6OPlAVxb"
MODEL = "deepseek-v4-flash-free"


def read_diff(path):
    if not path or not Path(path).exists():
        return "(no diff produced)"
    return Path(path).read_text()


SYSTEM_PROMPT = """You are a code review judge evaluating AI-generated kernel patches.

Given:
1. A task description (what the AI was asked to implement)
2. The expected/correct diff (ground truth)
3. Solution A (MCP-assisted) 
4. Solution B (baseline, no MCP)

Score each solution on:
- Correctness: Does it solve the described problem? No spurious changes?
- Completeness: Does it cover all needed hunks?
- Quality: Proper kernel coding style, no unnecessary changes

Then rank them 1st, 2nd, 3rd (expected always 1st if correct).

Respond with JSON only, no preamble:
{"ranking":["expected","A","B"],"scores":{"expected":{"correctness":10,"completeness":10,"quality":10,"total":30},"A":{"correctness":8,"completeness":7,"quality":9,"total":24},"B":{"correctness":6,"completeness":5,"quality":7,"total":18}},"reasoning":"Brief reasoning","best_match":"A"}"""


def call_llm(prompt):
    for attempt in range(3):
        try:
            payload = json.dumps({
                "model": MODEL,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": 8192,
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
            with urllib.request.urlopen(req, timeout=120) as resp:
                result = json.loads(resp.read())
            content = result["choices"][0]["message"]["content"].strip()
            if content:
                return content
        except Exception as e:
            if attempt < 2:
                import time
                time.sleep(5 * (2 ** attempt))
                continue
            return f"ERROR: {e}"
    return "ERROR: all retries exhausted"


def main():
    ap = argparse.ArgumentParser(description="Judge and rank patch solutions")
    ap.add_argument("--task", type=Path, required=True)
    ap.add_argument("--mcp-diff", type=Path)
    ap.add_argument("--baseline-diff", type=Path)
    ap.add_argument("--expected-diff", type=Path)
    ap.add_argument("--output", type=Path, default=Path("verdict.json"))
    args = ap.parse_args()

    task = json.loads(args.task.read_text())
    description = task.get("description", task.get("prompt", "?"))

    diff_a = read_diff(args.mcp_diff)
    diff_b = read_diff(args.baseline_diff)
    diff_exp = read_diff(args.expected_diff)

    # Truncate large diffs
    max_len = 8000
    if len(diff_a) > max_len:
        diff_a = diff_a[:max_len] + "\n...[truncated]"
    if len(diff_b) > max_len:
        diff_b = diff_b[:max_len] + "\n...[truncated]"
    if len(diff_exp) > max_len:
        diff_exp = diff_exp[:max_len] + "\n...[truncated]"

    prompt = f"""Task: {description}

Expected (ground truth):
```diff
{diff_exp}
```

Solution A (with fastcontext MCP):
```diff
{diff_a}
```

Solution B (baseline, no MCP):
```diff
{diff_b}
```

Rank and score each solution. Expected should be 1st unless it's wrong."""

    print("=== Running verifier ===", flush=True)
    result = call_llm(prompt)

    verdict = {}
    if result.startswith("ERROR:"):
        print(f"Verifier error: {result}", flush=True)
        verdict = {"error": result}
    else:
        try:
            m = re.search(r"\{.*\}", result, re.DOTALL)
            if m:
                verdict = json.loads(m.group())
            else:
                verdict = json.loads(result)
        except Exception as e:
            print(f"Verifier parse error: {e}", flush=True)
            verdict = {"raw": result}

    verdict["task"] = task.get("subject", "?")
    verdict["description"] = description

    args.output.write_text(json.dumps(verdict, indent=2))
    print(f"Verdict saved to {args.output}", flush=True)

    # Print summary
    rank = verdict.get("ranking", [])
    scores = verdict.get("scores", {})
    print(f"\nRanking: {rank}", flush=True)
    for s in rank:
        sc = scores.get(s, {})
        print(f"  {s}: total={sc.get('total','?')}", flush=True)
    print(f"Best match: {verdict.get('best_match', '?')}", flush=True)


if __name__ == "__main__":
    main()
