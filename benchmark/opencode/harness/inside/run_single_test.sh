#!/bin/bash
set -euo pipefail

MODE="${1:-}"
TASK_FILE="${TASK_FILE:-/task.json}"
LINUX_REPO="${LINUX_REPO:-/linux-repo}"
CONFIG_DIR="${CONFIG_DIR:-/config}"
RESULTS_DIR="${RESULTS_DIR:-/results}"
MODEL="${MODEL:-opencode/deepseek-v4-flash-free}"
TIMEOUT="${TIMEOUT:-300}"

if [[ -z "$MODE" || ( "$MODE" != "mcp" && "$MODE" != "baseline" ) ]]; then
    echo "Usage: $0 <mcp|baseline>"
    exit 1
fi

mkdir -p "$RESULTS_DIR" /root/.config/opencode

# Read task
COMMIT=$(python3 -c "import json; t=json.load(open('$TASK_FILE')); print(t['commit'])")
PARENT=$(python3 -c "import json; t=json.load(open('$TASK_FILE')); print(t['parent'])")
DESCRIPTION=$(python3 -c "import json; print(json.load(open('$TASK_FILE'))['description'])")
echo "=== Task: $DESCRIPTION ==="

# Prepare source in tmpfs (no .git at all)
echo "--- Preparing source (commit=$COMMIT parent=$PARENT) ---"
WORKDIR=$(mktemp -d)
git --git-dir="$LINUX_REPO/.git" archive --format=tar "$PARENT" | tar -x -C "$WORKDIR"

# Save expected diff
git --git-dir="$LINUX_REPO/.git" diff --no-color "${PARENT}..${COMMIT}" > "$RESULTS_DIR/expected.diff" 2>/dev/null || true
echo "  Expected diff saved ($(wc -c < "$RESULTS_DIR/expected.diff") bytes)"

# Mark timestamp so we can find files modified by opencode
touch "$WORKDIR/.extracted"

# Setup opencode config
if [ "$MODE" = "mcp" ]; then
    echo "=== Mode: with fastcontext MCP ==="
    echo "--- Syncing fastcontext dependencies ---"
    uv sync --no-dev --directory /fastcontext 2>&1 | tail -3
    python3 -c "
import json, os
with open('$CONFIG_DIR/opencode_mcp.json') as f:
    config = json.load(f)
env = config['mcp']['fastcontext']['env']
env['MODEL'] = os.environ.get('FASTCONTEXT_MODEL', '')
env['BASE_URL'] = os.environ.get('FASTCONTEXT_BASE_URL', '')
env['API_KEY'] = os.environ.get('FASTCONTEXT_API_KEY', '')
extra = os.environ.get('FASTCONTEXT_EXTRA_HEADERS')
if extra:
    env['EXTRA_HEADERS'] = extra
else:
    env.pop('EXTRA_HEADERS', None)
with open('/root/.config/opencode/opencode.json', 'w') as f:
    json.dump(config, f, indent=2)
"
else
    echo "=== Mode: baseline (no MCP) ==="
    cp "$CONFIG_DIR/opencode_baseline.json" /root/.config/opencode/opencode.json
fi

# Run opencode with JSON output — save full trajectory + extract tokens
echo "=== Running opencode (timeout: ${TIMEOUT}s) ==="
START_TIME=$(date +%s)
timeout "$TIMEOUT" opencode run --dangerously-skip-permissions --model "$MODEL" \
    --dir "$WORKDIR" --format json \
    "$DESCRIPTION" 2>/dev/null | tee "$RESULTS_DIR/trajectory.jsonl" | python3 -c "
import sys, json

total_in = 0
total_out = 0
total_reason = 0
cache_write = 0
cache_read = 0
cost = 0.0

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        ev = json.loads(line)
    except json.JSONDecodeError:
        print(line)
        continue
    
    ev_type = ev.get('type', '')
    part = ev.get('part', {})
    
    if ev_type == 'step_finish':
        t = part.get('tokens', {})
        if t:
            total_in += t.get('input', 0)
            total_out += t.get('output', 0)
            total_reason += t.get('reasoning', 0)
            cache_write += t.get('cache', {}).get('write', 0)
            cache_read += t.get('cache', {}).get('read', 0)
        cost += part.get('cost', 0) or 0
    
    if ev_type == 'tool_use':
        t = part.get('tool', '?')
        s = part.get('state', {})
        st = s.get('status', '')
        print(f'[{t}] ({st}) {s.get(\"title\", \"\")}'[:120])
    elif ev_type == 'step_finish':
        reason = part.get('reason', '')
        msg = part.get('message', '')[:80]
        print(f'[finish] reason={reason} {msg}')
    elif ev_type == 'completion':
        print('[DONE]')

with open('$RESULTS_DIR/tokens.json', 'w') as f:
    json.dump({
        'input_tokens': total_in,
        'output_tokens': total_out,
        'reasoning_tokens': total_reason,
        'cache_write': cache_write,
        'cache_read': cache_read,
        'total_tokens': total_in + total_out + total_reason,
        'cost': round(cost, 4),
    }, f, indent=2)

print(f'TOKENS: in={total_in} out={total_out} reasoning={total_reason} cache_w={cache_write} cache_r={cache_read} cost={cost:.4f}')
" > "$RESULTS_DIR/opencode_output.txt" || true
END_TIME=$(date +%s)
echo "Duration: $((END_TIME - START_TIME)) seconds" | tee -a "$RESULTS_DIR/opencode_output.txt"

# Collect sub-agent token usage from MCP trajectories (separate from main opencode log)
python3 -c "
import json, os, glob

workdir = '$WORKDIR'
results = '$RESULTS_DIR'
tokens_file = os.path.join(results, 'tokens.json')

# Load main tokens
with open(tokens_file) as f:
    t = json.load(f)

# Sum up tokens from all MCP sub-agent trajectories
mcp_in = mcp_out = mcp_reason = 0
for tj in glob.glob(os.path.join(workdir, '.fastcontext', 'mcp_trajectory_*.jsonl')):
    with open(tj) as f:
        for line in f:
            line = line.strip()
            if not line: continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get('type') != 'step_finish': continue
            tk = ev.get('part', {}).get('tokens', {})
            if not tk: continue
            mcp_in += tk.get('input', 0)
            mcp_out += tk.get('output', 0)
            mcp_reason += tk.get('reasoning', 0)

# Merge into main counts
if mcp_in or mcp_out or mcp_reason:
    t['mcp_input_tokens'] = mcp_in
    t['mcp_output_tokens'] = mcp_out
    t['mcp_reasoning_tokens'] = mcp_reason
    t['input_tokens'] = t.get('input_tokens', 0) + mcp_in
    t['output_tokens'] = t.get('output_tokens', 0) + mcp_out
    t['reasoning_tokens'] = t.get('reasoning_tokens', 0) + mcp_reason
    t['total_tokens'] = t.get('input_tokens', 0) + t.get('output_tokens', 0) + t.get('reasoning_tokens', 0)

with open(tokens_file, 'w') as f:
    json.dump(t, f, indent=2)
print(f'MCP sub-agent tokens: in={mcp_in} out={mcp_out} reasoning={mcp_reason}')
" 2>/dev/null || true

# Collect changes — diff each changed file against git originals (no re-extract)
echo "=== Collecting results ==="
cd /tmp
while IFS= read -r -d '' f; do
    rel="${f#$WORKDIR/}"
    if orig=$(git --git-dir="$LINUX_REPO/.git" show "$PARENT:$rel" 2>/dev/null); then
        diff -u --label "a/$rel" --label "b/$rel" <(printf '%s\n' "$orig") "$f" 2>/dev/null
    else
        diff -u --label "a/$rel" --label "b/$rel" /dev/null "$f" 2>/dev/null
    fi
done < <(find "$WORKDIR" -type f -newer "$WORKDIR/.extracted" -print0 2>/dev/null) > "$RESULTS_DIR/changes.diff" || true
grep '^diff ' "$RESULTS_DIR/changes.diff" > "$RESULTS_DIR/changes_stat.txt" 2>/dev/null || true

# Cleanup
rm -rf "$WORKDIR"

echo "=== Test complete ==="
exit 0
