from uagents import Agent, Context
from uagents_core.contrib.protocols.chat import ChatMessage, TextContent
import uuid
from pydantic import BaseModel
from typing import List
from enum import Enum
import instructor
from openai import OpenAI
from models import WorkerResponse, TaskRequest

client = instructor.from_openai(OpenAI(
    base_url="https://api.venice.ai/api/v1",
    api_key="VENICE_INFERENCE_KEY_-ZMGJZ9LK-Gnw-yh-BCecTz6UVBRzGkkrLx6npnF7K",
))

COMPLIANCE_ADDRESS    = "agent1q20clmt9u35lsnksu2tzjmpwtsl6wk0ef5vyyyydy46m8fh6jsqyklky4w5"
DATA_WORKER_ADDRESS   = "agent1qfvv4argh80yjlfuhpw6cy7wzj8cd4sc0txdh4du3alrag2qassuj93a9vc"
REPORT_WRITER_ADDRESS = "agent1qvw6qa4kvflsnn3g3aah8vfsnuga65y5a09c4tg4u2ehwprapsywzxzeh9r"

class WorkerType(str, Enum):
    compliance    = "compliance"
    data_worker   = "data_worker"
    report_writer = "report_writer"

WORKER_ADDRESSES = {
    WorkerType.compliance:    COMPLIANCE_ADDRESS,
    WorkerType.data_worker:   DATA_WORKER_ADDRESS,
    WorkerType.report_writer: REPORT_WRITER_ADDRESS,
}

class UserIntent(BaseModel):
    required_workers: List[WorkerType]

def generate_unique_id():
    return str(uuid.uuid4())

def analyze_intent(text: str) -> UserIntent:
    return client.chat.completions.create(
        model="llama-3.3-70b",
        response_model=UserIntent,
        max_tokens=256,
        messages=[
            {"role": "system", "content": (
                "Decide which worker(s) to call based on the user's question.\n\n"
                "Use ONLY 'report_writer' for:\n"
                "- Writing or generating a grant progress report\n"
                "- Summarising research work done, scientific progress, or lab activities\n"
                "- Requests like 'write a report', 'generate my progress report', "
                "'summarise my work', 'draft the ERC report'\n\n"
                "Use ONLY 'data_worker' for:\n"
                "- Factual lookups: amounts spent, transaction history, pending expenses, "
                "flight costs, equipment costs\n"
                "- Any 'how much', 'how many', 'list', 'show me', 'what did I spend' questions\n\n"
                "Use ONLY 'compliance' for:\n"
                "- Policy questions: is X allowed, is Y eligible, what are the rules for Z\n"
                "- Questions about the policy or compliance documents themselves: "
                "dates, versions, what they cover, effective dates\n\n"
                "Use BOTH 'data_worker' AND 'compliance' only when the user explicitly asks to:\n"
                "- Audit or validate expenses against policy "
                "(e.g. 'run an expense audit', 'check if my expenses are compliant')\n\n"
                "When in doubt about a simple factual question, use only 'data_worker'."
            )},
            {"role": "user", "content": text}
        ]
    )

def aggregate_results(results_list: list) -> str:
    # Logic: Join the data into a clean, professional summary

    report = "### Executive Summary\n\n"
    for item in results_list:
        report += f"- {item}\n"
    return report

# 1. State Store: Track ongoing requests
# This prevents the agent from forgetting what it's waiting for
request_states = {}

orchestrator = Agent(name="Orchestrator",
                         port=8000,
                         seed="orchestrator_secret_seed_phrase_2026",
                         endpoint=["http://127.0.0.1:8000/submit"],
                         mailbox="YOUR_ORCHESTRATOR_MAILBOX_KEY",
                         network="testnet")

@orchestrator.on_message(model=ChatMessage)
async def handle_user_message(ctx: Context, sender: str, msg: ChatMessage):
    text = next(c.text for c in msg.content if isinstance(c, TextContent))
    intent = analyze_intent(text)

    request_id = generate_unique_id()
    needs_data = WorkerType.data_worker in intent.required_workers
    needs_compliance = WorkerType.compliance in intent.required_workers

    if needs_data and needs_compliance:
        # Pipeline mode: fetch expense data first, then pass it into compliance query
        request_states[request_id] = {
            "pipeline": True,
            "pending_tasks": [DATA_WORKER_ADDRESS],
            "results": [],
            "original_query": text,
            "user_address": sender,
        }
        await ctx.send(DATA_WORKER_ADDRESS, TaskRequest(id=request_id, query=text))
    else:
        # Fan-out: dispatch all workers in parallel with the same query
        worker_addresses = [WORKER_ADDRESSES[w] for w in intent.required_workers]
        request_states[request_id] = {
            "pipeline": False,
            "pending_tasks": worker_addresses,
            "results": [],
            "original_query": text,
            "user_address": sender,
        }
        for address in worker_addresses:
            await ctx.send(address, TaskRequest(id=request_id, query=text))

@orchestrator.on_message(model=WorkerResponse)
async def handle_worker_reply(ctx: Context, sender: str, msg: WorkerResponse):
    state = request_states[msg.request_id]
    state["pending_tasks"].remove(sender)
    state["results"].append(msg.data)

    # Pipeline phase 2: data_worker just responded — enrich query and send to compliance
    if state["pipeline"] and sender == DATA_WORKER_ADDRESS:
        enriched_query = (
            f"{state['original_query']}\n\n"
            f"Here are the actual pending expenses to evaluate against policy:\n{msg.data}"
        )
        state["pending_tasks"].append(COMPLIANCE_ADDRESS)
        await ctx.send(COMPLIANCE_ADDRESS, TaskRequest(id=msg.request_id, query=enriched_query))
        return

    # All tasks done — aggregate and reply
    if not state["pending_tasks"]:
        final_answer = aggregate_results(state["results"])
        await ctx.send(state["user_address"], ChatMessage(content=[TextContent(text=final_answer)]))
        del request_states[msg.request_id]

if __name__ == "__main__":
    orchestrator.run()