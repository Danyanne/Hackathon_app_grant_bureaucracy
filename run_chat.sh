#!/usr/bin/env bash
# run_chat.sh — Start the full 6-agent swarm and open an interactive CLI chat.
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

# Kill any stale instances
pkill -f "python3.*agents/orchestrator.py"      2>/dev/null || true
pkill -f "python3.*agents/compliance_rag.py"    2>/dev/null || true
pkill -f "python3.*agents/data_worker.py"       2>/dev/null || true
pkill -f "python3.*agents/report_writer.py"     2>/dev/null || true
pkill -f "python3.*agents/email_drafter.py"     2>/dev/null || true
pkill -f "python3.*agents/budget_forecaster.py" 2>/dev/null || true
pkill -f "python3.*agents/chat.py"              2>/dev/null || true
sleep 1

python3 -u agents/orchestrator.py      > "$LOG_DIR/orchestrator.log"      2>&1 & agent_pids+=($!)
python3 -u agents/compliance_rag.py    > "$LOG_DIR/compliance.log"         2>&1 & agent_pids+=($!)
python3 -u agents/data_worker.py       > "$LOG_DIR/data_worker.log"        2>&1 & agent_pids+=($!)
python3 -u agents/report_writer.py     > "$LOG_DIR/report_writer.log"      2>&1 & agent_pids+=($!)
python3 -u agents/email_drafter.py     > "$LOG_DIR/email_drafter.log"      2>&1 & agent_pids+=($!)
python3 -u agents/budget_forecaster.py > "$LOG_DIR/budget_forecaster.log"  2>&1 & agent_pids+=($!)

AGENTS=(orchestrator compliance data_worker report_writer email_drafter budget_forecaster)
declare -A LOG_MAP=(
    [orchestrator]="orchestrator.log"
    [compliance]="compliance.log"
    [data_worker]="data_worker.log"
    [report_writer]="report_writer.log"
    [email_drafter]="email_drafter.log"
    [budget_forecaster]="budget_forecaster.log"
)

declare -A SEEN
READY=0
TIMEOUT=40
ELAPSED=0

echo "Starting 6-agent swarm..."

while [ $ELAPSED -lt $TIMEOUT ] && [ $READY -lt 6 ]; do
    sleep 1
    ELAPSED=$((ELAPSED + 1))
    for agent in "${AGENTS[@]}"; do
        [ -n "${SEEN[$agent]}" ] && continue
        log="$LOG_DIR/${LOG_MAP[$agent]}"
        if grep -q "Agent registration status updated to active\|Starting mailbox client" "$log" 2>/dev/null; then
            echo "  ✓  $agent online"
            SEEN[$agent]=1
            READY=$((READY + 1))
        elif grep -q "^ERROR\|address already in use" "$log" 2>/dev/null; then
            echo "  ✗  $agent failed — check logs/${LOG_MAP[$agent]}"
            SEEN[$agent]=1
        fi
    done
done

echo ""
[ $READY -lt 6 ] && echo "  ⚠  Only $READY/6 agents came online within ${TIMEOUT}s — check logs/"
echo "Opening chat..."
echo ""

python3 agents/chat.py
