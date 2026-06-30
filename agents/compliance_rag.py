from uagents import Agent, Context
from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings
from pathlib import Path
from pydantic import BaseModel
import instructor
from openai import OpenAI
from models import TaskRequest, WorkerResponse

client = instructor.from_openai(OpenAI(
    base_url="https://api.venice.ai/api/v1",
    api_key="VENICE_INFERENCE_KEY_-ZMGJZ9LK-Gnw-yh-BCecTz6UVBRzGkkrLx6npnF7K",
))

class ComplianceResponse(BaseModel):
    approved: bool
    explanation: str
    sources: list[str]

# Setup absolute paths
SCRIPT_DIR = Path(__file__).resolve().parent
DB_DIR = SCRIPT_DIR.parent / "chroma_db"

# 1. Initialize the Compliance Worker
compliance_oracle = Agent(
    name="ComplianceOracle",
    port=8001,
    seed="compliance_oracle_secret_seed_phrase_2026",
    endpoint=["http://127.0.0.1:8001/submit"],
    mailbox="YOUR_COMPLIANCE_MAILBOX_KEY",
    network="testnet"
)

# 2. Setup Vector Store
embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")
vector_db = Chroma(persist_directory=DB_DIR, embedding_function=embeddings)

@compliance_oracle.on_message(model=TaskRequest)
async def handle_compliance_query(ctx: Context, sender: str, msg: TaskRequest):
    ctx.logger.info(f"Query received: {msg.query}")

    # Perform retrieval
    results = vector_db.similarity_search(msg.query, k=3)
    context_text = "\n\n".join([doc.page_content for doc in results])

    response: ComplianceResponse = client.chat.completions.create(
        model="llama-3.3-70b",
        response_model=ComplianceResponse,
        max_tokens=512,
        messages=[
            {"role": "system", "content": (
                f"You are a helpful assistant with access to ERC grant and departmental financial policy documents. "
                f"Answer the user's question using the document excerpts below. "
                f"If the answer is in the excerpts, state it clearly and directly. "
                f"For compliance questions, also state whether the activity is approved or not. "
                f"Only say information is unavailable if it genuinely cannot be found in the excerpts.\n\n"
                f"DOCUMENT EXCERPTS:\n{context_text}"
            )},
            {"role": "user", "content": msg.query}
        ]
    )

    # For now, let's send a dummy response back to the Orchestrator
    await ctx.send(sender, WorkerResponse(request_id = msg.id, data=response.explanation))

if __name__ == "__main__":
    compliance_oracle.run()