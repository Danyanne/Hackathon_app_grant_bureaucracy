"""
compliance_rag.py — Compliance Oracle worker agent.

Searches the ChromaDB vector store of ERC policy documents and answers
whether activities/expenses are permitted under grant rules.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from uagents import Agent, Context
from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings
from openai import OpenAI

from models import TaskRequest, WorkerResponse
from config import (
    VENICE_API_KEY, VENICE_BASE_URL, VENICE_MODEL,
    AGENT_SEEDS, AGENT_PORTS, CHROMA_DB,
)

llm = OpenAI(base_url=VENICE_BASE_URL, api_key=VENICE_API_KEY)

compliance_oracle = Agent(
    name="ComplianceOracle",
    port=AGENT_PORTS["compliance"],
    seed=AGENT_SEEDS["compliance"],
    endpoint=[f"http://127.0.0.1:{AGENT_PORTS['compliance']}/submit"],
    mailbox=True,
)

try:
    embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")
    vector_db  = Chroma(persist_directory=str(CHROMA_DB), embedding_function=embeddings)
except Exception as _e:
    vector_db = None
    print(f"[compliance] WARNING: could not load vector DB: {_e}")


@compliance_oracle.on_message(model=TaskRequest)
async def handle_compliance_query(ctx: Context, sender: str, msg: TaskRequest):
    ctx.logger.info(f"[compliance] query: {msg.query[:60]}…")

    if vector_db is None:
        await ctx.send(sender, WorkerResponse(
            request_id=msg.id,
            data="⚠️ Compliance database not available. Run `python3 data_ingestor.py` first.",
        ))
        return

    try:
        results      = vector_db.similarity_search(msg.query, k=3)
        context_text = "\n\n".join(doc.page_content for doc in results)
    except Exception as e:
        await ctx.send(sender, WorkerResponse(
            request_id=msg.id,
            data=f"⚠️ Could not search policy documents: {e}",
        ))
        return

    # Split out any uploaded document text from the question
    if "UPLOADED DOCUMENT CONTENT:" in msg.query:
        question, _, uploaded = msg.query.partition("UPLOADED DOCUMENT CONTENT:")
        uploaded_block = f"\n\nUPLOADED DOCUMENT (treat as primary source):\n{uploaded.strip()}"
    else:
        question = msg.query
        uploaded_block = ""

    try:
        _r = llm.chat.completions.create(
            model=VENICE_MODEL,
            max_tokens=2000,
            messages=[
                {"role": "system", "content": (
                    "You are a compliance advisor for ERC grants with access to a knowledge base "
                    "of ERC/Horizon Europe policy documents and departmental financial policies. "
                    "The relevant excerpts from that knowledge base are provided below — treat them "
                    "as authoritative and cite them confidently.\n\n"
                    "RULES:\n"
                    "- If the excerpts below contain the answer, use them and cite the source/section. "
                    "Do NOT second-guess or disclaim information that is clearly present in the excerpts.\n"
                    "- Never invent specific figures, section numbers, names, or deadlines that are NOT "
                    "in the excerpts below.\n"
                    "- If the excerpts do not cover the question, say: "
                    "'My knowledge base doesn't have the specific policy for this — you should check "
                    "[institution HR / ERC Grant Agreement Annex 2 / national labour law].' "
                    "Do not say you have 'no documents' — you have general ERC policy knowledge.\n"
                    "- For yes/no compliance questions, start with ✅ Approved or ❌ Not approved "
                    "when the excerpts clearly support it.\n"
                    "- If an uploaded document is provided, use it as the primary source.\n\n"
                    f"KNOWLEDGE BASE EXCERPTS:\n{context_text}"
                    f"{uploaded_block}"
                )},
                {"role": "user", "content": question.strip()},
            ],
        )
        response = (_r.choices[0].message.content or "").strip()
    except Exception as e:
        response = f"⚠️ Compliance agent LLM error: {e}"

    await ctx.send(sender, WorkerResponse(request_id=msg.id, data=response))


if __name__ == "__main__":
    compliance_oracle.run()
