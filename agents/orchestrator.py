"""
orchestrator.py — Central routing agent for the Scientist Personal Assistant.

Registered on Agentverse with the Agent Chat Protocol so it can be discovered
and used directly through ASI:One without any custom frontend.

Architecture:
  ASI:One / user
       │  ChatMessage (Agent Chat Protocol)
       ▼
  Orchestrator  ──── TaskRequest ────►  compliance_rag
       │         ──── TaskRequest ────►  data_worker
       │         ──── TaskRequest ────►  report_writer
       │         ──── TaskRequest ────►  email_drafter
       │         ──── TaskRequest ────►  budget_forecaster
       │
       └── synthesises WorkerResponses → single ChatMessage reply
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uuid
import time
from datetime import datetime, timezone

from uagents import Agent, Context, Protocol
from uagents_core.contrib.protocols.chat import (
    ChatMessage,
    ChatAcknowledgement,
    TextContent,
    ResourceContent,
    StartSessionContent,
    EndSessionContent,
    chat_protocol_spec,
)
from openai import OpenAI

import requests as _requests
from models import TaskRequest, WorkerResponse
from config import (
    VENICE_API_KEY, VENICE_BASE_URL, VENICE_MODEL,
    COMPLIANCE_ADDRESS, DATA_WORKER_ADDRESS, REPORT_WRITER_ADDRESS,
    EMAIL_DRAFTER_ADDRESS, BUDGET_FORECASTER_ADDRESS,
    AGENT_SEEDS,
    PAYMENT_ENABLED, REPORT_COST_FET, PAYMENT_WALLET,
)

llm = OpenAI(base_url=VENICE_BASE_URL, api_key=VENICE_API_KEY)

# ── Routing ───────────────────────────────────────────────────────────────────

_VALID_WORKERS = {
    "compliance", "data_worker", "report_writer",
    "email_drafter", "budget_forecaster",
}

WORKER_ADDRESSES = {
    "compliance":        COMPLIANCE_ADDRESS,
    "data_worker":       DATA_WORKER_ADDRESS,
    "report_writer":     REPORT_WRITER_ADDRESS,
    "email_drafter":     EMAIL_DRAFTER_ADDRESS,
    "budget_forecaster": BUDGET_FORECASTER_ADDRESS,
}

WORKER_LABELS = {
    "compliance":        "Compliance Oracle",
    "data_worker":       "Data Analyst",
    "report_writer":     "Report Writer",
    "email_drafter":     "Email Drafter",
    "budget_forecaster": "Budget Forecaster",
}

_ROUTING_PROMPT = """\
You are a routing agent for a Scientist Personal Assistant that manages ERC grants.
Reply with ONLY a comma-separated list of worker names chosen from:
compliance, data_worker, report_writer, email_drafter, budget_forecaster

compliance        — grant policy, eligibility rules, what expenses are allowed
data_worker       — expense lookups, financial records, how much was spent (read-only)
report_writer     — writing progress reports, GitHub activity, lab notes, research summaries
email_drafter     — composing any professional email (to ERC officers, collaborators, reviewers)
budget_forecaster — future budget projections, burn rate, will money last, upcoming purchases

Priority rules:
1. Any request to write/draft/send an email → email_drafter
2. Forward-looking budget questions (projections, burn rate, forecast) → budget_forecaster
3. Expense + policy question together → data_worker,compliance
4. GitHub/repo questions (last commit, recent changes, repo updates) → report_writer
5. Greetings or off-topic → none

Examples:
  'hi' → none
  'can I buy a GPU with ERC funds?' → compliance
  'how much did we spend on travel?' → data_worker
  'write a Q2 progress report' → report_writer
  'when was the last commit?' → report_writer
  'what was the last update in the repo?' → report_writer
  'what has the team been working on?' → report_writer
  'draft an email to the ERC officer' → email_drafter
  'will we stay within budget?' → budget_forecaster
  'is this flight expense allowed?' → data_worker,compliance

Reply only with worker name(s) or 'none'. No explanation."""


def analyze_intent(text: str) -> list[str]:
    import re
    _tl    = text.lower()
    _words = set(_tl.split())
    _action = {"draft", "write", "compose", "send", "prepare"}
    _email_w = {"email", "mail", "e-mail"}
    # Fast-path: drafting verb + "email/mail" anywhere, or email address + action verb
    if (
        (_action & _words and _email_w & _words)
        or (re.search(r"[\w.+\-]+@[\w\-]+\.[a-zA-Z]{2,}", text) and _action & _words)
    ):
        return ["email_drafter"]

    try:
        _r = llm.chat.completions.create(
            model=VENICE_MODEL,
            max_tokens=32,
            temperature=0,
            messages=[
                {"role": "system", "content": _ROUTING_PROMPT},
                {"role": "user",   "content": text},
            ],
        )
        reply = (_r.choices[0].message.content or "").strip().lower()
        if not reply or reply == "none":
            return []
        return [w.strip() for w in reply.split(",") if w.strip() in _VALID_WORKERS]
    except Exception:
        return []


# ── Agent & Chat Protocol ─────────────────────────────────────────────────────

orchestrator = Agent(
    name="ScientistPersonalAssistant",
    seed=AGENT_SEEDS["orchestrator"],
    mailbox=True,
    network="testnet",
)

# Build a Protocol from the official spec so Agentverse recognises it
chat_proto = Protocol(spec=chat_protocol_spec)

# In-flight request tracking
request_states: dict = {}
payment_pending: dict = {}
REQUEST_TIMEOUT_S = 90  # seconds before a worker is assumed offline

# Conversation history keyed by sender address — last 10 exchanges kept
conversation_history: dict[str, list[dict]] = {}
_MAX_HISTORY = 10  # message pairs to retain per user

def _get_history(sender: str) -> list[dict]:
    return conversation_history.setdefault(sender, [])

def _push_history(sender: str, role: str, content: str) -> None:
    h = conversation_history.setdefault(sender, [])
    h.append({"role": role, "content": content})
    # keep last _MAX_HISTORY*2 entries (user+assistant pairs)
    if len(h) > _MAX_HISTORY * 2:
        conversation_history[sender] = h[-(  _MAX_HISTORY * 2):]


async def _reply(ctx: Context, to: str, text: str) -> None:
    await ctx.send(to, ChatMessage(content=[TextContent(text=text)]))


_EXT_DIR = {
    ".pdf":  "knowledge_base",
    ".xlsx": "knowledge_base",
    ".xls":  "knowledge_base",
    ".md":   "lab_notes",
    ".txt":  "lab_notes",
    ".tex":  "papers",
    ".bib":  "papers",
}

def _extract_pdf_text(path: Path) -> str:
    """Extract plain text from a PDF for immediate use in this query."""
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        return "\n\n".join(page.extract_text() or "" for page in reader.pages)[:8000]
    except Exception:
        return ""


def _reingest_chromadb():
    """Re-run the ChromaDB ingestor in a background thread after new files land."""
    import threading
    def _run():
        try:
            import sys as _sys
            _sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
            from data_ingestor import ingest_documents
            ingest_documents()
        except Exception as e:
            print(f"[orchestrator] ChromaDB re-ingest failed: {e}")
    threading.Thread(target=_run, daemon=True).start()


def _save_uploaded_files(msg: ChatMessage) -> tuple[list[str], str]:
    """Download ResourceContent files, save to the right directory, and extract
    text from PDFs for immediate use.
    Returns (saved_descriptions, extracted_text_block)."""
    import base64, re as _re
    from config import PROJECT_ROOT
    saved: list[str] = []
    extracted_texts: list[str] = []
    has_pdf = False

    for item in msg.content:
        if not isinstance(item, ResourceContent):
            continue
        resources = item.resource if isinstance(item.resource, list) else [item.resource]
        for res in resources:
            uri      = str(res.uri)
            meta     = res.metadata or {}
            filename = meta.get("filename") or meta.get("name") or uri.split("/")[-1] or "upload"
            # strip query strings from filename if it came from a URL
            filename = filename.split("?")[0] or "upload"
            if not Path(filename).suffix:
                filename += ".pdf"
            ext      = Path(filename).suffix.lower()
            subdir   = _EXT_DIR.get(ext, "knowledge_base")
            dest_dir = PROJECT_ROOT / subdir
            dest_dir.mkdir(exist_ok=True)
            dest = dest_dir / filename

            raw_bytes: bytes | None = None
            try:
                if uri.startswith("data:"):
                    # data URI — decode base64 payload
                    header, _, b64 = uri.partition(",")
                    raw_bytes = base64.b64decode(b64 + "==")  # extra == is safe
                else:
                    r = _requests.get(uri, timeout=30)
                    r.raise_for_status()
                    raw_bytes = r.content
            except Exception as e:
                print(f"[orchestrator] file download failed for {filename}: {e}")
                saved.append(f"`{filename}` — download failed: {e}")
                continue

            dest.write_bytes(raw_bytes)
            saved.append(f"`{filename}` → {subdir}/")
            print(f"[orchestrator] saved {filename} ({len(raw_bytes)} bytes) to {dest}")

            if ext == ".pdf":
                has_pdf = True
                text = _extract_pdf_text(dest)
                print(f"[orchestrator] extracted {len(text)} chars from {filename}")
                if text.strip():
                    extracted_texts.append(f"=== {filename} ===\n{text}")
                else:
                    print(f"[orchestrator] WARNING: PDF text extraction returned empty for {filename}")
            elif ext in {".md", ".txt"}:
                extracted_texts.append(f"=== {filename} ===\n{dest.read_text(errors='replace')[:4000]}")

    if has_pdf:
        _reingest_chromadb()

    return saved, "\n\n".join(extracted_texts)


# ── Handlers on the chat protocol (not directly on the agent) ─────────────────

@chat_proto.on_message(model=ChatAcknowledgement)
async def handle_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
    pass  # ACKs from ASI:One don't need processing


@chat_proto.on_message(model=ChatMessage)
async def handle_user_message(ctx: Context, sender: str, msg: ChatMessage):
    # Immediate ACK — required by ASI:One
    ack_id = getattr(msg, "msg_id", str(uuid.uuid4()))
    await ctx.send(sender, ChatAcknowledgement(
        timestamp=datetime.now(timezone.utc),
        acknowledged_msg_id=ack_id,
    ))

    # Handle session lifecycle events
    for item in msg.content:
        if isinstance(item, StartSessionContent):
            ctx.logger.info(f"[orchestrator] session started with {sender[:20]}")
        elif isinstance(item, EndSessionContent):
            ctx.logger.info(f"[orchestrator] session ended with {sender[:20]}")
            conversation_history.pop(sender, None)  # clean up history on session end

    # Handle any uploaded files — save, extract text, trigger re-ingest
    saved_files, file_text = _save_uploaded_files(msg)
    if saved_files:
        note = "📎 Files received and saved:\n" + "\n".join(f"  • {s}" for s in saved_files)
        if file_text:
            note += "\n_(Text extracted — using it to answer your question now.)_"
        else:
            note += "\n_(Ingesting into knowledge base for future queries.)_"
        await _reply(ctx, sender, note)

    text = next((c.text for c in msg.content if isinstance(c, TextContent)), "").strip()
    if not text and not saved_files:
        return
    if not text:
        return

    # Append extracted file text to the query so workers see it immediately
    if file_text:
        text = f"{text}\n\nUPLOADED DOCUMENT CONTENT:\n{file_text}"

    ctx.logger.info(f"[orchestrator] received: {text[:80]}")

    if text.lower() in ("confirm", "yes", "pay") and sender in payment_pending:
        text = payment_pending.pop(sender)["query"]

    # Record user turn before routing
    _push_history(sender, "user", text)

    workers = analyze_intent(text)

    # If a document was uploaded and routing is uncertain, default to compliance
    if file_text and not workers:
        workers = ["compliance"]
        ctx.logger.info("[orchestrator] uploaded document → defaulting to compliance")
    ctx.logger.info(f"[orchestrator] routing to: {workers or ['generic']}")

    if not workers:
        try:
            history = _get_history(sender)
            # history already includes the current user turn, so pass all of it
            messages = [
                {"role": "system", "content": (
                    "You are a Scientist Personal Assistant specialising in ERC grant management. "
                    "Help scientists with compliance, expenses, report writing, email drafting, "
                    "and budget forecasting. Be concise and warm. "
                    "If greeted, briefly introduce yourself and your capabilities."
                )},
                *history,
            ]
            _r = llm.chat.completions.create(
                model=VENICE_MODEL,
                max_tokens=1500,
                messages=messages,
            )
            reply = (_r.choices[0].message.content or "").strip()
        except Exception as e:
            reply = f"I'm having trouble reaching the AI backend right now: {e}"
        _push_history(sender, "assistant", reply)
        await _reply(ctx, sender, reply)
        return

    is_full_report = "report_writer" in workers and any(
        k in text.lower() for k in ("write", "draft", "generate", "report")
    )
    if PAYMENT_ENABLED and is_full_report and sender not in payment_pending:
        payment_pending[sender] = {"query": text}
        await _reply(ctx, sender,
            f"📄 **Full ERC Progress Report**\n\n"
            f"Generating a complete structured report is a premium operation.\n\n"
            f"**Cost:** {REPORT_COST_FET} FET\n"
            f"**Send to:** `{PAYMENT_WALLET}`\n\n"
            f"Reply **confirm** once payment is sent to proceed.\n"
            f"_(Ask a specific question instead for a free quick answer.)_"
        )
        return

    labels = " · ".join(WORKER_LABELS.get(w, w) for w in workers)
    await _reply(ctx, sender, f"🔍 Consulting {labels}…")

    request_id = str(uuid.uuid4())

    # Prepend recent conversation context to worker queries so workers understand references
    prior = _get_history(sender)[:-1]  # all but the current user turn
    if prior:
        ctx_lines = []
        for m in prior[-6:]:  # last 3 exchanges
            prefix = "User" if m["role"] == "user" else "Assistant"
            ctx_lines.append(f"{prefix}: {m['content'][:300]}")
        context_prefix = "CONVERSATION CONTEXT (for reference):\n" + "\n".join(ctx_lines) + "\n\nCURRENT QUERY:\n"
        worker_query = context_prefix + text
    else:
        worker_query = text

    if "data_worker" in workers and "compliance" in workers:
        request_states[request_id] = {
            "mode":          "pipeline",
            "pipeline_done": False,
            "pending":       [DATA_WORKER_ADDRESS],
            "results":       [],
            "query":         worker_query,
            "user":          sender,
            "created_at":    time.monotonic(),
        }
        await ctx.send(DATA_WORKER_ADDRESS, TaskRequest(id=request_id, query=worker_query))
    else:
        addresses = [WORKER_ADDRESSES[w] for w in workers if w in WORKER_ADDRESSES]
        if not addresses:
            await _reply(ctx, sender, "No available worker for that request yet.")
            return
        request_states[request_id] = {
            "mode":       "fanout",
            "pending":    list(addresses),
            "results":    [],
            "query":      worker_query,
            "user":       sender,
            "created_at": time.monotonic(),
        }
        for addr in addresses:
            await ctx.send(addr, TaskRequest(id=request_id, query=worker_query))


# ── Worker responses (on agent directly — internal protocol) ──────────────────

@orchestrator.on_message(model=WorkerResponse)
async def handle_worker_reply(ctx: Context, sender: str, msg: WorkerResponse):
    rid = msg.request_id
    if rid not in request_states:
        return

    state = request_states[rid]
    if sender in state["pending"]:
        state["pending"].remove(sender)
    state["results"].append(msg.data)

    ctx.logger.info(f"[orchestrator] reply from {sender[:20]}…, {len(state['pending'])} pending")

    if (state["mode"] == "pipeline"
            and not state["pipeline_done"]
            and sender == DATA_WORKER_ADDRESS):
        state["pipeline_done"] = True
        enriched = f"{state['query']}\n\nExpense data from records:\n{msg.data}"
        state["pending"].append(COMPLIANCE_ADDRESS)
        await ctx.send(COMPLIANCE_ADDRESS, TaskRequest(id=rid, query=enriched))
        return

    if not state["pending"]:
        results = state["results"]
        if len(results) == 1:
            final = results[0]
        else:
            try:
                _r = llm.chat.completions.create(
                    model=VENICE_MODEL,
                    max_tokens=2000,
                    messages=[
                        {"role": "system", "content": (
                            "You are a grant management assistant. Two specialist agents answered "
                            "the same question from different angles. Combine their findings into "
                            "one clear, direct answer. No section headers. Start by answering directly."
                        )},
                        {"role": "user", "content": (
                            f"Question: {state['query']}\n\n"
                            + "\n\n---\n\n".join(results)
                        )},
                    ],
                )
                final = (_r.choices[0].message.content or "").strip()
            except Exception:
                final = "\n\n---\n\n".join(results)

        _push_history(state["user"], "assistant", final)
        await _reply(ctx, state["user"], final)
        del request_states[rid]


# ── Stale request cleanup (runs every 30 s) ────────────────────────────────────

@orchestrator.on_interval(period=30.0)
async def cleanup_stale_requests(ctx: Context) -> None:
    """Reply with a fallback message for any request that hasn't resolved within
    REQUEST_TIMEOUT_S seconds, then remove it from request_states."""
    now = time.monotonic()
    stale = [
        rid for rid, st in request_states.items()
        if now - st.get("created_at", now) > REQUEST_TIMEOUT_S
    ]
    for rid in stale:
        state = request_states.pop(rid)
        ctx.logger.warning(f"[orchestrator] request {rid[:8]}… timed out — notifying user")
        await _reply(
            ctx, state["user"],
            "⚠️ One or more specialist agents didn't respond in time. "
            "Please try again — if the issue persists, the agent may be restarting."
        )


# Include the chat protocol — this publishes the manifest so Agentverse
# recognises the agent as chat-capable.
orchestrator.include(chat_proto, publish_manifest=True)


if __name__ == "__main__":
    orchestrator.run()
