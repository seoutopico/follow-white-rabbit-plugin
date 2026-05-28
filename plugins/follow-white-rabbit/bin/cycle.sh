#!/bin/bash
# Deterministic research orchestrator.
# Spawns one claude worker per topic, checks targets, retries shortfalls.
# No LLM judgment in orchestration — every topic gets a worker, every time.

set -euo pipefail

# Project directory = wherever the user invoked cycle.sh from (their feed project).
# NOT the directory where cycle.sh itself lives (the plugin cache).
PROJECT_DIR="$(pwd)"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
FEED_PY="$SCRIPT_DIR/feed.py"
PUBLISH_SH="$SCRIPT_DIR/publish.sh"
CONFIG_PATH="$PROJECT_DIR/config.yaml"

if [ ! -f "$CONFIG_PATH" ]; then
    echo "ERROR: config.yaml not found at $CONFIG_PATH" >&2
    echo "       cycle.sh must be run from your project directory" >&2
    echo "       (where config.yaml lives). For cron/launchd, set the" >&2
    echo "       working directory to your project dir." >&2
    exit 1
fi
if [ ! -f "$FEED_PY" ]; then
    echo "ERROR: feed.py not found at $FEED_PY (plugin install broken?)" >&2
    exit 1
fi

# --- Config ---
CLAUDE_BIN="${CLAUDE_BIN:-claude}"
PYTHON="${PYTHON:-python3}"
WORKER_TIMEOUT="${WORKER_TIMEOUT:-900}"   # 15 min per worker
TIMEOUT_BIN="${TIMEOUT_BIN:-timeout}"

# --- Log rotation ---
mkdir -p .logs
LOG_FILE=".logs/research-$(date +%Y-%m-%d).log"
exec >> "$LOG_FILE" 2>&1

# Clean up logs older than 7 days
find .logs -name "research-*.log" -mtime +7 -delete 2>/dev/null || true
find .logs -name "*_round*.log" -mtime +7 -delete 2>/dev/null || true
find .logs -name "*_retry*.log" -mtime +7 -delete 2>/dev/null || true

# --- Guaranteed publish on exit ---
PUBLISHED=0
cleanup_publish() {
    if [ "$PUBLISHED" -eq 0 ]; then
        echo ""
        echo "--- Emergency publish (orchestration did not complete normally) ---"
        $PYTHON "$FEED_PY" prune --keep 50 || true
        local base_url
        base_url=$($PYTHON -c "
import yaml
with open(r'$CONFIG_PATH') as f:
    print(yaml.safe_load(f).get('settings',{}).get('base_url',''))
" 2>/dev/null || true)
        if [ -n "$base_url" ]; then
            bash "$PUBLISH_SH" "$base_url" || true
        fi
    fi
}
trap cleanup_publish EXIT

# Parse topics from config.yaml: topic_id|target
# (model field is no longer read here: scout uses sonnet, writer uses opus, fixed in the agent defs.)
TOPICS=$($PYTHON -c "
import yaml
with open(r'$CONFIG_PATH') as f:
    config = yaml.safe_load(f)
for t in config.get('topics', []):
    print(f\"{t['id']}|{t.get('target',0)}\")
")

# Init all feed XMLs
$PYTHON "$FEED_PY" init

# Generate run ID and prepare findings dir for the scout phase
RUN_ID=$(date -u +%Y-%m-%dT%H:%M:%SZ)
FINDINGS_ROOT="$PROJECT_DIR/.state/findings/$RUN_ID"
mkdir -p "$FINDINGS_ROOT"
echo "=== Research cycle: $RUN_ID ==="
echo ""

# Run one phase (scout or writer) for a batch of topics in parallel.
# Inputs: phase_name, agent_model, round_name, then lines "topic_id|target|extra_prompt".
# Echoes the topic_ids that succeeded (one per line) to stdout for the caller.
run_phase() {
    local phase_name="$1"
    local agent_model="$2"
    local round_name="$3"
    shift 3
    local topics_to_run=("$@")

    local pids=()
    local topic_ids=()
    local tools
    if [ "$phase_name" = "scout" ]; then
        tools="WebSearch,WebFetch,Bash,Read,Grep,Glob,Write"
    else
        tools="Bash,Read,Grep,Glob,WebFetch"
    fi

    for line in "${topics_to_run[@]}"; do
        IFS='|' read -r topic_id target_or_gap extra_prompt <<< "$line"
        local findings_path="$FINDINGS_ROOT/${topic_id}.json"
        echo "  [$round_name/$phase_name] $topic_id (model=$agent_model)" >&2

        local prompt
        if [ "$phase_name" = "scout" ]; then
            local covered
            covered=$($PYTHON "$FEED_PY" state "$topic_id" 2>/dev/null | sed -n '/^=== RECENTLY COVERED/,/^===/p' || true)
            prompt="@research-scout Process topic '$topic_id' with run-id '$RUN_ID'. Your target is $target_or_gap findings.
Write your findings JSON to: $findings_path"
            if [ -n "$covered" ]; then
                prompt="$prompt

$covered
Do NOT include findings about subjects above unless you have genuinely new facts."
            fi
            if [ -n "$extra_prompt" ]; then
                prompt="$prompt

$extra_prompt"
            fi
        else
            prompt="@research-writer Process topic '$topic_id' with run-id '$RUN_ID'.
Findings file (already produced by the scout): $findings_path
Read it, write each finding as an entry following the topic brief and quality rules, then update knowledge and log."
        fi

        "$TIMEOUT_BIN" --kill-after=30 "$WORKER_TIMEOUT" \
            "$CLAUDE_BIN" --model "$agent_model" -p "$prompt" \
            --allowedTools "$tools" \
            --permission-mode dontAsk \
            > "$PROJECT_DIR/.logs/${topic_id}_${round_name}_${phase_name}.log" 2>&1 &
        pids+=($!)
        topic_ids+=("$topic_id")
    done

    echo "  Waiting for ${#pids[@]} ${phase_name}s (timeout: ${WORKER_TIMEOUT}s)..." >&2
    local i=0
    for pid in "${pids[@]}"; do
        local exit_code=0
        wait "$pid" || exit_code=$?
        local tid="${topic_ids[$i]}"
        if [ "$exit_code" -eq 124 ] || [ "$exit_code" -eq 137 ]; then
            echo "  TIMEOUT: $tid (killed after ${WORKER_TIMEOUT}s)" >&2
        elif [ "$exit_code" -ne 0 ]; then
            echo "  FAILED: $tid (exit $exit_code)" >&2
        else
            echo "  OK: $tid" >&2
            echo "$tid"   # success line on stdout
        fi
        i=$((i + 1))
    done
    echo "  [$round_name/$phase_name] Done." >&2
    echo "" >&2
}

# Orchestrate one round: scout (Sonnet) parallel, then writer (Opus) parallel
# for topics where the scout produced a non-empty findings file.
spawn_workers() {
    local round_name="$1"
    shift
    local topics_to_run=("$@")

    # Phase A: scout
    local scout_ok
    scout_ok=$(run_phase "scout" "sonnet" "$round_name" "${topics_to_run[@]}")

    # Phase B: writer — only for topics where scout succeeded AND wrote a non-empty findings JSON
    local write_lines=()
    while IFS= read -r line; do
        [ -z "$line" ] && continue
        IFS='|' read -r topic_id target_or_gap extra_prompt <<< "$line"
        local findings_path="$FINDINGS_ROOT/${topic_id}.json"
        if ! grep -qx "$topic_id" <<< "$scout_ok"; then
            echo "  SKIP writer/$topic_id (scout failed)" >&2
            continue
        fi
        if [ ! -f "$findings_path" ]; then
            echo "  SKIP writer/$topic_id (scout did not produce $findings_path)" >&2
            continue
        fi
        # Quick check: if findings array is empty, skip writer
        local n_findings
        n_findings=$($PYTHON -c "
import json, sys
try:
    d = json.load(open(r'$findings_path'))
    print(len(d.get('findings', [])))
except Exception:
    print(0)
")
        if [ "$n_findings" = "0" ]; then
            echo "  SKIP writer/$topic_id (scout returned 0 findings)" >&2
            continue
        fi
        write_lines+=("$line")
    done <<< "$(printf '%s\n' "${topics_to_run[@]}")"

    if [ ${#write_lines[@]} -eq 0 ]; then
        echo "  [$round_name] No topics with findings — round done." >&2
        return
    fi

    run_phase "writer" "opus" "$round_name" "${write_lines[@]}" > /dev/null
}

# === Round 1: All topics ===
echo "--- Round 1: Spawning all workers ---"
round1_topics=()
while IFS= read -r line; do
    round1_topics+=("${line}|")  # empty extra_prompt
done <<< "$TOPICS"

spawn_workers "round1" "${round1_topics[@]}"

# === Check targets ===
echo "--- Checking targets ---"
CHECK_OUTPUT=$($PYTHON "$FEED_PY" check-targets --run-id "$RUN_ID" 2>&1) || true
echo "$CHECK_OUTPUT"
echo ""

if echo "$CHECK_OUTPUT" | grep -q "__SHORTFALLS_JSON__"; then
    # Parse shortfalls
    SHORTFALLS_JSON=$(echo "$CHECK_OUTPUT" | grep "__SHORTFALLS_JSON__" | sed 's/.*__SHORTFALLS_JSON__://')

    RETRY_LINES=$($PYTHON -c "
import json, yaml, sys
shortfalls = json.loads(sys.argv[1])
with open(r'$CONFIG_PATH') as f:
    config = yaml.safe_load(f)
topic_map = {t['id']: t for t in config.get('topics', [])}
for s in shortfalls:
    extra = f\"Previous round produced {s['added']}/{s['target']}. Try to produce {s['gap']} more entries by searching different sub-topics and broadening scope. Do NOT re-cover subjects already in state — check the RECENTLY COVERED list. If you cannot find genuinely new subjects after thorough searching, producing fewer is OK.\"
    print(f\"{s['topic_id']}|{s['gap']}|{extra}\")
" "$SHORTFALLS_JSON")

    # === Round 2: Retry shortfalls ===
    echo "--- Round 2: Retrying shortfall topics ---"
    retry_topics=()
    while IFS= read -r line; do
        retry_topics+=("$line")
    done <<< "$RETRY_LINES"

    spawn_workers "retry" "${retry_topics[@]}"

    # Final check (informational)
    echo "--- Final target check ---"
    $PYTHON "$FEED_PY" check-targets --run-id "$RUN_ID" 2>&1 || true
    echo ""
else
    echo "All targets met on first round!"
fi

# === Prune, render and publish ===
echo "--- Pruning, rendering, publishing ---"
$PYTHON "$FEED_PY" prune --keep 50
BASE_URL=$($PYTHON -c "
import yaml
with open(r'$CONFIG_PATH') as f:
    print(yaml.safe_load(f).get('settings',{}).get('base_url',''))
")
$PYTHON "$FEED_PY" index-html     --base-url "$BASE_URL"
$PYTHON "$FEED_PY" opml            --base-url "$BASE_URL"
$PYTHON "$FEED_PY" render-html     --base-url "$BASE_URL"
$PYTHON "$FEED_PY" render-archive  --base-url "$BASE_URL"
$PYTHON "$FEED_PY" render-readme   --base-url "$BASE_URL"
bash "$PUBLISH_SH" "$BASE_URL"
PUBLISHED=1
echo ""
echo "=== Done ==="
