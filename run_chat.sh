#!/usr/bin/env bash
set -e

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

source venv/bin/activate

LOG_DIR="$PROJECT_ROOT/logs"
mkdir -p "$LOG_DIR"

agent_pids=()

cleanup() {
    echo ""
    echo "Shutting down agents..."
    for pid in "${agent_pids[@]}"; do
        kill "$pid" 2>/dev/null || true
    done
}
trap cleanup EXIT INT TERM

# Kill any stale instances from previous runs
pkill -f "python3.*agents/orchestrator.py"   2>/dev/null || true
pkill -f "python3.*agents/compliance_rag.py" 2>/dev/null || true
pkill -f "python3.*agents/data_worker.py"    2>/dev/null || true
pkill -f "python3.*agents/report_writer.py"  2>/dev/null || true
pkill -f "python3.*agents/trigger_swarm.py"  2>/dev/null || true
pkill -f "python3.*agents/chat.py"           2>/dev/null || true
sleep 1

# Start the 4 server agents silently — logs go to files only
python3 -u agents/orchestrator.py   > "$LOG_DIR/orchestrator.log"   2>&1 & agent_pids+=($!)
python3 -u agents/compliance_rag.py > "$LOG_DIR/compliance_rag.log" 2>&1 & agent_pids+=($!)
python3 -u agents/data_worker.py    > "$LOG_DIR/data_worker.log"    2>&1 & agent_pids+=($!)
python3 -u agents/report_writer.py  > "$LOG_DIR/report_writer.log"  2>&1 & agent_pids+=($!)

# Poll each agent's log until it registers or times out
declare -A SEEN
READY=0
TIMEOUT=30
ELAPSED=0

echo ""
echo "Starting agents..."

while [ $ELAPSED -lt $TIMEOUT ] && [ $READY -lt 4 ]; do
    sleep 0.5
    ELAPSED=$((ELAPSED + 1))

    for agent in orchestrator compliance_rag data_worker report_writer; do
        [ -n "${SEEN[$agent]}" ] && continue
        log="$LOG_DIR/$agent.log"

        if grep -q "Agent registration status updated to active" "$log" 2>/dev/null; then
            echo "  ✓  $agent is online"
            SEEN[$agent]=1
            READY=$((READY + 1))
        elif grep -q "^ERROR\|address already in use" "$log" 2>/dev/null; then
            echo "  ✗  $agent failed to start — check logs/$agent.log"
            SEEN[$agent]=1
        fi
    done
done

if [ $READY -lt 4 ]; then
    echo ""
    echo "  ⚠  Not all agents came online within ${TIMEOUT}s."
    echo "     Check the logs/ directory for details."
fi

echo ""

# Run the interactive chat in the foreground
python3 agents/chat.py
