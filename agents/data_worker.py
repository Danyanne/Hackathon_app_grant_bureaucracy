from uagents import Agent, Context, Model
import pandas as pd
import requests
from pathlib import Path
from openai import OpenAI
from models import TaskRequest, WorkerResponse

llm = OpenAI(
    base_url="https://api.venice.ai/api/v1",
    api_key="VENICE_INFERENCE_KEY_-ZMGJZ9LK-Gnw-yh-BCecTz6UVBRzGkkrLx6npnF7K",
)

# Communication Contract
class DataLogQuery(Model):
    task_type: str  # 'progress_report' or 'expense_audit'

class DataLogResponse(Model):
    content: str

data_worker = Agent(
    name="DataWorker",
    port=8002,
    seed="data_worker_secret_seed_phrase_2026",
    endpoint=["http://127.0.0.1:8002/submit"],
    mailbox="YOUR_DATA_WORKER_MAILBOX_KEY",
    network="testnet"
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
EXCEL_PATH = PROJECT_ROOT / "knowledge_base" / "erc_solar_physics_expenses.xlsx"

@data_worker.on_message(model=TaskRequest)
async def handle_query(ctx: Context, sender: str, msg: TaskRequest):
    # Orchestrator already decided this query belongs to data_worker.
    # Default to expense_audit; only override for explicit GitHub/progress queries.
    if "commit" in msg.query.lower() or "progress" in msg.query.lower():
        task_type = "progress_report"
    else:
        task_type = "expense_audit"

    ctx.logger.info(f"Processing {task_type}...")

    # Logic for Expense Audit — pass all data to LLM, let it answer naturally
    if task_type == "expense_audit":
        df = pd.read_excel(EXCEL_PATH)
        data_str = df.to_string(index=False)
        result = llm.chat.completions.create(
            model="llama-3.3-70b",
            max_tokens=512,
            messages=[
                {"role": "system", "content": (
                    "You are a data analyst. Answer the user's question based solely on the "
                    "expense records below. Be specific, concise, and reference exact values "
                    "from the data.\n\nEXPENSE RECORDS:\n" + data_str
                )},
                {"role": "user", "content": msg.query}
            ]
        )
        response = result.choices[0].message.content

    # Logic for Progress Report (GitHub API)
    elif task_type == "progress_report":
        # Replace with your actual repo details
        resp = requests.get("https://api.github.com/repos/YOUR_ORG/YOUR_REPO/commits")
        commits = [c['commit']['message'] for c in resp.json()[:5]]
        response = f"Recent development progress:\n- " + "\n- ".join(commits)

    else:
        response = "I can only look up expense records or GitHub progress reports. For questions about policy documents, please ask the compliance oracle."

    await ctx.send(sender, WorkerResponse(request_id = msg.id, data=response))

if __name__ == "__main__":
    data_worker.run()