#!/usr/bin/env bash
set -e

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

source venv/bin/activate

LOG_DIR="$PROJECT_ROOT/logs"
mkdir -p "$LOG_DIR"

agent_pids=()
tail_pids=()

cleanup() {
    echo ""
    echo "Shutting down all agents..."
    for pid in "${agent_pids[@]}"; do
        kill "$pid" 2>/dev/null || true
    done
    for pid in "${tail_pids[@]}"; do
        kill "$pid" 2>/dev/null || true
    done
}
trap cleanup EXIT INT TERM

# Kill any stale instances from previous runs
pkill -f "python3.*agents/orchestrator.py"   2>/dev/null || true
pkill -f "python3.*agents/compliance_rag.py" 2>/dev/null || true
pkill -f "python3.*agents/data_worker.py"    2>/dev/null || true
pkill -f "python3.*agents/trigger_swarm.py"  2>/dev/null || true
sleep 1

# Launch an agent in background and tail its log live with a prefix
start_agent() {
    local name="$1"
    local script="$2"
    local log="$LOG_DIR/$name.log"

    python3 -u "agents/$script" > "$log" 2>&1 &
    agent_pids+=($!)

    # Stream log lines to terminal with agent name prefix
    tail -n 0 -F "$log" 2>/dev/null | sed -u "s/^/[${name}] /" &
    tail_pids+=($!)
}

echo "Starting agents..."
start_agent "orchestrator"   "orchestrator.py"
start_agent "compliance"     "compliance_rag.py"
start_agent "data_worker"    "data_worker.py"

echo "Waiting for agents to register on Almanac (10s)..."
sleep 10

echo "Firing trigger..."
echo ""
start_agent "trigger" "trigger_swarm.py"

# Keep script alive until Ctrl+C
wait
