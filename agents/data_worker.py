"""
data_worker.py — Expense data analyst worker agent.

Reads the ERC expense spreadsheet and answers financial questions:
totals, per-category breakdowns, specific transaction lookups.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from uagents import Agent, Context
from openai import OpenAI

from models import TaskRequest, WorkerResponse
from config import (
    VENICE_API_KEY, VENICE_BASE_URL, VENICE_MODEL,
    AGENT_SEEDS, AGENT_PORTS, KNOWLEDGE_BASE,
)

llm = OpenAI(base_url=VENICE_BASE_URL, api_key=VENICE_API_KEY)

EXCEL_PATH = KNOWLEDGE_BASE / "erc_solar_physics_expenses.xlsx"

data_worker = Agent(
    name="DataWorker",
    port=AGENT_PORTS["data_worker"],
    seed=AGENT_SEEDS["data_worker"],
    endpoint=[f"http://127.0.0.1:{AGENT_PORTS['data_worker']}/submit"],
    mailbox=True,
)


@data_worker.on_message(model=TaskRequest)
async def handle_query(ctx: Context, sender: str, msg: TaskRequest):
    ctx.logger.info(f"[data_worker] query: {msg.query[:60]}…")

    if not EXCEL_PATH.exists():
        await ctx.send(sender, WorkerResponse(
            request_id=msg.id,
            data="⚠️ Expense spreadsheet not found in knowledge_base/.",
        ))
        return

    try:
        import pandas as pd
        df       = pd.read_excel(EXCEL_PATH)
        data_str = df.to_string(index=False)
    except Exception as e:
        await ctx.send(sender, WorkerResponse(
            request_id=msg.id,
            data=f"⚠️ Could not read expense spreadsheet: {e}",
        ))
        return

    try:
        _r = llm.chat.completions.create(
            model=VENICE_MODEL,
            max_tokens=1000,
            messages=[
                {"role": "system", "content": (
                    "You are a financial data analyst. Answer the user's question based solely "
                    "on the expense records below. Be specific and reference exact values.\n\n"
                    f"EXPENSE RECORDS:\n{data_str}"
                )},
                {"role": "user", "content": msg.query},
            ],
        )
        result = (_r.choices[0].message.content or "").strip()
    except Exception as e:
        result = f"⚠️ Data worker LLM error: {e}"

    await ctx.send(sender, WorkerResponse(request_id=msg.id, data=result))


if __name__ == "__main__":
    data_worker.run()
