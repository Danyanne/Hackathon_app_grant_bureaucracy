"""
streamlit_app.py — Grant Bureaucracy Assistant web UI.

Single-file Streamlit application.  All worker logic runs in-process; the
uAgents swarm (run_chat.sh / run_swarm.sh) is not required for the UI.

Tab layout:
    💬 Chat      — multi-agent chat with routing to compliance / data / report workers
    🏛️ Grants    — grant cards with milestones, burn rate, budget planner, publications
    📅 Deadlines — LLM-extracted deadline list and calendar view
    ✍️ Apply     — grant proposal section generator
    📂 Files     — file upload, ingestion, and Overleaf ZIP importer
"""
import sys
import json
from datetime import datetime
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "agents"))

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Scientist Personal Assistant",
    page_icon="🔬",
    layout="wide",
)

# ── Cached resources (load once per session) ──────────────────────────────────

from config import VENICE_API_KEY, VENICE_BASE_URL, VENICE_MODEL

@st.cache_resource(show_spinner="Loading language model...")
def get_llm():
    from openai import OpenAI
    return OpenAI(base_url=VENICE_BASE_URL, api_key=VENICE_API_KEY)

@st.cache_resource(show_spinner="Loading instructor client...")
def get_instructor_client():
    import instructor
    from openai import OpenAI
    return instructor.from_openai(OpenAI(base_url=VENICE_BASE_URL, api_key=VENICE_API_KEY))

@st.cache_resource(show_spinner="Loading compliance vector database...")
def get_vector_db():
    from langchain_chroma import Chroma
    from langchain_huggingface import HuggingFaceEmbeddings
    DB_DIR = ROOT / "chroma_db"
    if not DB_DIR.exists():
        return None
    embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")
    return Chroma(persist_directory=str(DB_DIR), embedding_function=embeddings)

@st.cache_data(show_spinner=False)
def load_registry() -> dict:
    reg_path = ROOT / "data" / "worker_registry.json"
    if reg_path.exists():
        return json.loads(reg_path.read_text(encoding="utf-8"))
    return {}

# ── Routing ───────────────────────────────────────────────────────────────────

_VALID_WORKERS = {"compliance", "data_worker", "data_editor", "report_writer", "email_drafter", "budget_forecaster"}

def analyze_intent(text: str) -> list[str]:
    """Return the list of worker names the query should be routed to.

    Uses a plain-text LLM call (no structured output) to avoid token-limit
    issues with Venice AI's JSON schema enforcement.  Returns an empty list
    for greetings or off-topic messages so dispatch() falls back to a direct
    conversational reply.

    Fast-path: if the message contains an email address and email-related
    keywords, route directly to email_drafter without an LLM call.
    """
    import re as _re
    _tl    = text.lower()
    _words = set(_tl.split())
    _action  = {"draft", "write", "compose", "send", "prepare"}
    _email_w = {"email", "mail", "e-mail"}
    if (
        (_action & _words and _email_w & _words)
        or (_re.search(r"[\w.+\-]+@[\w\-]+\.[a-zA-Z]{2,}", text) and _action & _words)
    ):
        return ["email_drafter"]

    prompt = (
        "You are a routing agent. Reply with ONLY a comma-separated list of worker names "
        "chosen from: compliance, data_worker, data_editor, report_writer, email_drafter, budget_forecaster.\n\n"
        "compliance        — grant policy rules, eligibility, what is/isn't allowed\n"
        "data_worker       — expense lookups, how much was spent, financial data (READ only)\n"
        "data_editor       — adding, recording, or updating an expense row in the spreadsheet\n"
        "report_writer     — research progress, GitHub activity, lab notes, writing reports\n"
        "email_drafter     — drafting, writing, composing, or sending ANY email\n"
        "budget_forecaster — forward-looking budget questions: projections, burn rate, will we run out, "
        "what will future costs look like, upcoming purchases, budget forecast\n\n"
        "PRIORITY RULES (apply in order):\n"
        "1. If the user asks to write/draft/compose/send an email (even about expenses), "
        "ALWAYS use email_drafter — never data_worker.\n"
        "2. Use budget_forecaster for forward-looking / projection questions about budget.\n"
        "3. Use data_editor when user wants to ADD, RECORD, LOG, or UPDATE an expense row.\n"
        "4. Use data_worker for READ-only historical financial questions: totals, summaries, lookups.\n"
        "5. Use data_worker AND compliance together only for explicit compliance audits.\n"
        "6. For research/GitHub/lab questions, use report_writer.\n"
        "7. For greetings, small talk, or anything unrelated, reply with exactly: none\n\n"
        "Examples:\n"
        "  'hi' → none\n"
        "  'can I buy a laptop?' → compliance\n"
        "  'how much did I spend on travel?' → data_worker\n"
        "  'add a travel expense of €500' → data_editor\n"
        "  'is the flight expense compliant?' → data_worker,compliance\n"
        "  'write my Q2 report' → report_writer\n"
        "  'what was the last commit?' → report_writer\n"
        "  'when was the last update in the repo?' → report_writer\n"
        "  'what has the team been working on lately?' → report_writer\n"
        "  'draft an email to the ERC officer about our progress' → email_drafter\n"
        "  'send an email to dc1824@ic.ac.uk to check last quarter expenses' → email_drafter\n"
        "  'compose a recruitment email for a postdoc' → email_drafter\n"
        "  'will we stay within budget?' → budget_forecaster\n"
        "  'forecast remaining spend for the grant' → budget_forecaster\n"
        "  'what is our burn rate and will the money last?' → budget_forecaster\n"
        "  'are there any upcoming purchases in the lab notes?' → budget_forecaster\n\n"
        "Reply with only the worker name(s) or 'none'. No explanation."
    )

    try:
        llm   = get_llm()
        reply = llm.chat.completions.create(
            model=VENICE_MODEL,
            max_tokens=32,
            temperature=0,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user",   "content": text},
            ],
        ).choices[0].message.content.strip().lower()
    except Exception:
        return []   # routing failure → generic fallback in dispatch()

    if reply == "none" or not reply:
        return []
    return [w.strip() for w in reply.split(",") if w.strip() in _VALID_WORKERS]

# ── Worker implementations ────────────────────────────────────────────────────

REPORT_KEYWORDS = {"report", "write", "draft", "generate", "summarise", "summarize"}


def run_compliance(query: str) -> str:
    """Query the ChromaDB vector store with `query`, then ask the LLM to answer
    using the retrieved policy document excerpts.

    Returns a markdown string.  Prepends a "✅ Approved" / "❌ Not approved"
    badge only when the question explicitly asks whether a specific activity is
    permitted AND the documents contain a clear answer; otherwise returns the
    explanation alone.
    """
    from pydantic import BaseModel

    from typing import Optional

    class ComplianceResponse(BaseModel):
        approved: Optional[bool]  # None when the question is not a yes/no compliance check
        explanation: str
        sources: list[str]

    vdb = get_vector_db()
    if vdb is None:
        return "⚠️ Compliance database not found. Run `python3 data_ingestor.py` to index policy documents."
    try:
        results      = vdb.similarity_search(query, k=3)
        context_text = "\n\n".join(doc.page_content for doc in results)
    except Exception as e:
        return f"⚠️ Could not search compliance documents: {e}"
    try:
        client   = get_instructor_client()
        response: ComplianceResponse = client.chat.completions.create(
            model=VENICE_MODEL,
            response_model=ComplianceResponse,
            max_tokens=512,
            messages=[
                {"role": "system", "content": (
                    "You are a helpful assistant with access to ERC grant and departmental "
                    "financial policy documents. Answer the user's question using the excerpts below. "
                    "Set 'approved' to true/false ONLY when the question asks whether a specific "
                    "activity or expense is permitted AND the documents contain a clear answer. "
                    "If the question is informational, or if the documents do not address the "
                    "specific activity, leave 'approved' null.\n\n"
                    f"DOCUMENT EXCERPTS:\n{context_text}"
                )},
                {"role": "user", "content": query},
            ],
        )
    except Exception as e:
        return f"⚠️ Compliance agent error: {e}"
    if response.approved is None:
        return response.explanation
    status = "✅ Approved" if response.approved else "❌ Not approved"
    return f"**{status}**\n\n{response.explanation}"


def run_data_worker(query: str) -> str:
    """Answer a read-only question about the expense spreadsheet.

    Reads the full Excel file into a string and passes it to the LLM as
    context.  Suitable for totals, summaries, and lookups; not for writes
    (use run_data_editor for those).
    """
    import pandas as pd
    xls_path = ROOT / "knowledge_base" / "erc_solar_physics_expenses.xlsx"
    if not xls_path.exists():
        return "⚠️ Expense spreadsheet not found in knowledge_base/."
    try:
        df       = pd.read_excel(xls_path)
        data_str = df.to_string(index=False)
    except Exception as e:
        return f"⚠️ Could not read expense spreadsheet: {e}"
    try:
        llm    = get_llm()
        result = llm.chat.completions.create(
            model=VENICE_MODEL,
            max_tokens=512,
            messages=[
                {"role": "system", "content": (
                    "You are a data analyst. Answer the user's question based solely on the "
                    "expense records below. Be specific and reference exact values.\n\n"
                    f"EXPENSE RECORDS:\n{data_str}"
                )},
                {"role": "user", "content": query},
            ],
        )
        return result.choices[0].message.content
    except Exception as e:
        return f"⚠️ Data worker error: {e}"


_XLS_PATH = ROOT / "knowledge_base" / "erc_solar_physics_expenses.xlsx"

_ERC_BUDGET_LINES = {
    "Personnel":       "A.1. Staff",
    "Compute":         "B.2. Consumables",
    "Travel":          "B.1. Travel",
    "Equipment":       "B.3. Equipment",
    "Subcontracting":  "B.4. Other",
    "Other":           "B.2. Consumables",
}

def run_data_editor(query: str) -> tuple[dict | None, str]:
    """Parse a natural-language write request into a proposed expense row.

    Asks the LLM to extract structured fields from the user's message, then
    returns a preview markdown string for display in the chat.  The row is
    NOT written to disk here; writing happens in _apply_expense_row() after
    the user confirms (and optionally edits) in the UI.

    Returns:
        (row_dict, preview_markdown) — row_dict is None if parsing failed.
    """
    import pandas as pd, json as _json
    from datetime import date as _date

    if not _XLS_PATH.exists():
        return None, "Expense spreadsheet not found."

    df = pd.read_excel(_XLS_PATH)
    # Next transaction ID
    ids = [r for r in df["Transaction_ID"].tolist() if isinstance(r, str) and r.startswith("TRX-")]
    next_num = max((int(r.split("-")[-1]) for r in ids if r.split("-")[-1].isdigit()), default=200) + 1
    next_id  = f"TRX-{_date.today().year}-{next_num}"

    try:
        llm = get_llm()
        raw = llm.chat.completions.create(
            model=VENICE_MODEL,
            max_tokens=256,
            temperature=0,
            messages=[
                {"role": "system", "content": (
                    "Extract expense details from the user's message and return ONLY a JSON object "
                    "with these exact keys: date (YYYY-MM-DD), category, description, amount_eur (number), "
                    "erc_budget_line, compliance_status.\n"
                    f"Today is {_date.today()}. Use today's date if none given.\n"
                    "Category must be one of: Personnel, Compute, Travel, Equipment, Subcontracting, Other.\n"
                    f"ERC budget line should match the category: {_ERC_BUDGET_LINES}.\n"
                    "compliance_status: use 'Approved' unless the item seems unusual, then 'Pending Audit'.\n"
                    "Return ONLY the JSON, no explanation."
                )},
                {"role": "user", "content": query},
            ],
        ).choices[0].message.content.strip()
    except Exception as e:
        return None, f"⚠️ Expense editor error: {e}"

    try:
        # Strip markdown code fences if present
        raw = raw.strip("` \n")
        if raw.startswith("json"):
            raw = raw[4:]
        parsed = _json.loads(raw)
    except Exception:
        return None, f"Sorry, I couldn't parse that as an expense entry. Please be more specific, e.g. *'Add a travel expense of €850 for AGU conference on 2026-12-10'*."

    row = {
        "Transaction_ID":    next_id,
        "Date":              parsed.get("date", str(_date.today())),
        "Category":          parsed.get("category", "Other"),
        "Description":       parsed.get("description", query),
        "Amount_EUR":        float(parsed.get("amount_eur", 0)),
        "ERC_Budget_Line":   parsed.get("erc_budget_line", "B.2. Consumables"),
        "Compliance_Status": parsed.get("compliance_status", "Pending Audit"),
    }

    preview = (
        f"I'll add this row to the expense table:\n\n"
        f"| Field | Value |\n|---|---|\n"
        f"| ID | `{row['Transaction_ID']}` |\n"
        f"| Date | {row['Date']} |\n"
        f"| Category | {row['Category']} |\n"
        f"| Description | {row['Description']} |\n"
        f"| Amount | €{row['Amount_EUR']:,.2f} |\n"
        f"| Budget line | {row['ERC_Budget_Line']} |\n"
        f"| Status | {row['Compliance_Status']} |\n\n"
        f"Confirm to save, or cancel."
    )
    return row, preview


def _apply_expense_row(row: dict):
    """Append `row` to the expense Excel file in-place."""
    import pandas as pd
    df  = pd.read_excel(_XLS_PATH)
    df  = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    df.to_excel(_XLS_PATH, index=False)


def run_report_writer(query: str) -> str:
    """Delegate to worker_logic.generate_report_response().

    Full-report queries (containing "write", "draft", "report", etc.) produce
    a structured document saved to reports/ and return a confirmation prefix.
    Factual queries (e.g. "when was the last commit?") return a direct answer.
    """
    try:
        from worker_logic import generate_report_response
        report_text, saved_path = generate_report_response(query)
    except Exception as e:
        return f"⚠️ Report writer error: {e}"
    if saved_path:
        rel = saved_path.split("Hackathon_app_grant_bureaucracy/")[-1]
        return f"**Report saved to `{rel}`**\n\n---\n\n{report_text}"
    return report_text


def run_budget_forecaster(query: str) -> str:
    """Forecast future budget consumption from spending history + lab-note signals.

    Data sources used:
    - Expense spreadsheet: historical spending per category and month
    - Lab notes: freeform mentions of planned purchases, upcoming needs
    - grants.json: total budget, grant end date, milestones
    """
    import sys as _sys
    import pandas as _pd
    from datetime import datetime as _dt

    _sys.path.insert(0, str(ROOT / "agents"))
    from worker_logic import load_lab_notes

    # ── Expense history ───────────────────────────────────────────────────────
    xls = ROOT / "knowledge_base" / "erc_solar_physics_expenses.xlsx"
    expense_summary = "(No expense data found)"
    if xls.exists():
        try:
            df = _pd.read_excel(xls)
            total_spent = df["Amount_EUR"].sum() if "Amount_EUR" in df.columns else 0
            by_cat = (
                df.groupby("Category")["Amount_EUR"].sum().sort_values(ascending=False)
                if "Category" in df.columns else _pd.Series(dtype=float)
            )
            by_month = ""
            if "Date" in df.columns:
                df["_month"] = _pd.to_datetime(df["Date"], errors="coerce").dt.to_period("M")
                monthly = df.groupby("_month")["Amount_EUR"].sum().sort_index()
                by_month = "\nMonthly spend:\n" + "\n".join(
                    f"  {m}: EUR {v:,.0f}" for m, v in monthly.items()
                )
            cat_lines = "\n".join(
                f"  {cat}: EUR {val:,.0f}" for cat, val in by_cat.items()
            )
            expense_summary = (
                f"Total spent to date: EUR {total_spent:,.0f}\n"
                f"By category:\n{cat_lines}"
                + by_month
            )
        except Exception as e:
            expense_summary = f"(Could not read expense data: {e})"

    # ── Grant context ─────────────────────────────────────────────────────────
    grants = load_grants()
    grant_ctx = ""
    for g in grants:
        budget   = g.get("total_budget_eur", 0)
        start    = g.get("start_date", "")
        end      = g.get("end_date", "")
        ms_lines = [
            f"  {m.get('id')} — {m.get('title')} [{m.get('status','planned')}] due {m.get('due_date','')}"
            for m in g.get("milestones", [])
        ]
        grant_ctx += (
            f"Grant: {g['title']} ({g['id']})\n"
            f"Total budget: EUR {budget:,}\n"
            f"Period: {start} to {end}\n"
            f"Milestones:\n" + "\n".join(ms_lines)
        )
        # Days remaining
        try:
            days_left = (_dt.strptime(end, "%Y-%m-%d") - _dt.today()).days
            grant_ctx += f"\nDays remaining: {days_left}\n"
        except Exception:
            pass

    # ── Lab notes (planned purchases / upcoming needs) ────────────────────────
    lab_notes = load_lab_notes()
    if lab_notes == "(No lab notes found)":
        lab_notes = "(No lab notes available)"

    try:
        llm = get_llm()
        result = llm.chat.completions.create(
            model=VENICE_MODEL,
            max_tokens=900,
            messages=[
                {"role": "system", "content": (
                    "You are a financial analyst embedded in a scientific research team. "
                    "Your job is to forecast the remaining budget consumption for an ERC grant. "
                    "Use the expense history to identify monthly burn rate and trends per category. "
                    "Scan the lab notes for any mentions of planned purchases, equipment needs, "
                    "upcoming travel, conferences, or hiring intentions — these are forward-looking signals. "
                    "Combine both to produce a structured forecast: "
                    "(1) current burn rate and trajectory, "
                    "(2) signals from lab notes about upcoming costs, "
                    "(3) projected remaining spend by category, "
                    "(4) whether the grant is on track to stay within budget, "
                    "(5) any risk flags. "
                    "Be specific with numbers. If the lab notes mention something (e.g. 'we need a new GPU'), "
                    "include it as a forecast item with a rough cost estimate if possible.\n\n"
                    f"GRANT DETAILS:\n{grant_ctx}\n\n"
                    f"EXPENSE HISTORY:\n{expense_summary}\n\n"
                    f"LAB NOTES:\n{lab_notes[:3000]}"
                )},
                {"role": "user", "content": query},
            ],
        ).choices[0].message.content.strip()
        return result
    except Exception as e:
        return f"⚠️ Budget forecaster error: {e}"


# ── File ingestion helpers ────────────────────────────────────────────────────

DEST_DIRS = {
    "Policy document (PDF)":  ROOT / "knowledge_base",
    "Expense data (Excel)":   ROOT / "knowledge_base",
    "Lab notes (.md / .txt)": ROOT / "lab_notes",
    "Email thread (.txt)":    ROOT / "emails",
}

ACCEPTED_EXTENSIONS = {
    "Policy document (PDF)":  [".pdf"],
    "Expense data (Excel)":   [".xlsx", ".xls"],
    "Lab notes (.md / .txt)": [".md", ".txt"],
    "Email thread (.txt)":    [".txt"],
}


def _ingest_pdf_to_chroma(pdf_path: Path) -> int:
    """Add a PDF to the ChromaDB vector store. Returns number of chunks added."""
    import pypdf
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    from langchain_core.documents import Document

    reader = pypdf.PdfReader(str(pdf_path))
    pages  = [
        Document(
            page_content=page.extract_text() or "",
            metadata={"source": str(pdf_path), "page": i},
        )
        for i, page in enumerate(reader.pages)
    ]
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000, chunk_overlap=200,
        separators=["\n## ", "\n### ", "\n\n", "\n", ".", " "],
    )
    chunks = splitter.split_documents(pages)
    vdb = get_vector_db()
    if vdb is not None:
        vdb.add_documents(chunks)
    return len(chunks)


def _extract_overleaf_zip(uploaded_zip) -> tuple[list[str], int]:
    """
    Extracts .tex (and .bib) files from an Overleaf ZIP export.
    Saves them to papers/  Returns (list of saved names, total count).
    """
    import zipfile, io
    papers_dir = ROOT / "papers"
    papers_dir.mkdir(exist_ok=True)

    saved = []
    with zipfile.ZipFile(io.BytesIO(uploaded_zip.getvalue())) as zf:
        for member in zf.infolist():
            if member.filename.endswith(("/", "\\")):
                continue
            ext = Path(member.filename).suffix.lower()
            if ext not in {".tex", ".bib"}:
                continue
            # flatten path — keep only the filename
            dest_name = Path(member.filename).name
            dest = papers_dir / dest_name
            dest.write_bytes(zf.read(member.filename))
            saved.append(dest_name)
    return saved, len(saved)


def save_uploaded_file(uploaded_file, category: str) -> Path:
    dest_dir = DEST_DIRS[category]
    dest_dir.mkdir(exist_ok=True)
    dest = dest_dir / uploaded_file.name
    dest.write_bytes(uploaded_file.getvalue())
    return dest


def rebuild_registry_ui() -> str:
    """Run build_registry.build_registry() and return a status string."""
    sys.path.insert(0, str(ROOT))
    import importlib
    import build_registry
    importlib.reload(build_registry)        # pick up any newly saved files
    build_registry.build_registry()
    load_registry.clear()                   # bust the st.cache_data cache
    return "Registry updated."


# ── Dispatch ──────────────────────────────────────────────────────────────────

def run_email_drafter(query: str) -> tuple[str, dict]:
    """Generate a professional email from a natural-language request.

    Builds grant/milestone/budget context, generates the email via LLM, then
    returns (preview_text, pending_action_dict) so the chat can display an
    editable draft with an Open-in-mail-app button.
    """
    import re

    # Parse a recipient email address from the query if present
    _email_re = re.search(r"[\w.+\-]+@[\w\-]+\.[a-zA-Z]{2,}", query)
    recipient_email = _email_re.group(0) if _email_re else ""

    # Build plain-text context block — no special Unicode, no Python objects
    ctx_lines: list[str] = []
    grants = load_grants()
    pi_name = ""
    for g in grants:
        if not pi_name:
            pi_name = g.get("pi", "")
        ctx_lines.append(f"Grant title: {g['title']}")
        ctx_lines.append(f"Grant ID: {g['id']}")
        ctx_lines.append(f"Funder: {g['funder']}")
        ctx_lines.append(f"PI: {g.get('pi', 'Unknown')}")
        ctx_lines.append(f"Period: {g['start_date']} to {g['end_date']}")
        budget = g.get("total_budget_eur", 0)
        ctx_lines.append(f"Total budget: EUR {budget:,}")
        ctx_lines.append(f"Status: {g.get('status', 'unknown')}")
        milestones = g.get("milestones", [])
        if milestones:
            ctx_lines.append("Milestones:")
            for m in milestones:
                mid    = m.get("id", "")
                mtitle = m.get("title", "")
                mstat  = m.get("status", "planned")
                mdue   = m.get("due_date", "")
                ctx_lines.append(f"  - {mid}: {mtitle} | status: {mstat} | due: {mdue}")

    xls = ROOT / "knowledge_base" / "erc_solar_physics_expenses.xlsx"
    if xls.exists():
        try:
            import pandas as _pd
            _df = _pd.read_excel(xls)
            if "Amount_EUR" in _df.columns:
                spent = float(_df["Amount_EUR"].sum())
                if grants:
                    b = grants[0].get("total_budget_eur", 0)
                    ctx_lines.append(f"Budget spent so far: EUR {spent:,.0f} of EUR {b:,} (EUR {b - spent:,.0f} remaining)")
            ctx_lines.append("\nFULL EXPENSE RECORDS:")
            ctx_lines.append(_df.to_string(index=False))
        except Exception:
            pass

    context_block = "\n".join(ctx_lines)

    try:
        llm = get_llm()
        raw = llm.chat.completions.create(
            model=VENICE_MODEL,
            max_tokens=900,
            messages=[
                {"role": "system", "content": (
                    "You are a professional scientific communications assistant. "
                    "Write a professional email based on the researcher's request. "
                    "Output ONLY the email — no preamble, no commentary. "
                    "First line must be: Subject: <subject line>\n"
                    "Then a blank line, then the email body with proper salutation and sign-off. "
                    f"Sign off with the PI's name: {pi_name or 'the PI'}.\n\n"
                    f"PROJECT CONTEXT:\n{context_block}"
                )},
                {"role": "user", "content": query},
            ],
        ).choices[0].message.content.strip()
    except Exception as e:
        err = f"⚠️ Email drafter error: {e}"
        return err, {"type": "email_draft", "content": err, "subject": "Error", "recipient": recipient_email}

    # Extract subject for preview
    subject = "Email draft"
    for line in raw.splitlines():
        if line.lower().startswith("subject:"):
            subject = line[8:].strip()
            break

    preview = f"✉️ **Email draft ready** — {subject}"
    return preview, {
        "type": "email_draft",
        "content": raw,
        "subject": subject,
        "recipient": recipient_email,
    }


def dispatch(query: str) -> tuple[str, list[str], dict | None]:
    """Route a user message to the appropriate worker(s) and return the answer.

    Pipeline mode: when both data_worker and compliance are needed, the data
    worker runs first and its output is injected into the compliance query so
    the policy check has real expense figures.

    Returns:
        answer          — markdown string for display
        workers_used    — list of worker names that contributed
        pending_action  — expense row dict (data_editor), email draft dict (email_drafter), or None
    """
    workers = analyze_intent(query)

    if "data_editor" in workers:
        row, preview = run_data_editor(query)
        return preview, ["data_editor"], row

    if "data_worker" in workers and "compliance" in workers:
        data_answer = run_data_worker(query)
        enriched    = f"{query}\n\nExpense data:\n{data_answer}"
        comp_answer = run_compliance(enriched)
        try:
            llm = get_llm()
            synthesis = llm.chat.completions.create(
                model=VENICE_MODEL,
                max_tokens=600,
                messages=[
                    {"role": "system", "content": (
                        "You are a grant management assistant helping a scientist. "
                        "Two specialist agents have analysed the user's question — one looked up "
                        "expense records, the other checked grant compliance rules. "
                        "Combine their findings into a single, direct, conversational answer. "
                        "Do NOT use section headers. Weave the financial data and policy "
                        "information together naturally. Start by directly answering the question."
                    )},
                    {"role": "user", "content": (
                        f"Question: {query}\n\n"
                        f"Expense records analysis:\n{data_answer}\n\n"
                        f"Compliance / policy analysis:\n{comp_answer}"
                    )},
                ],
            ).choices[0].message.content.strip()
        except Exception:
            # Synthesis failed — fall back to showing both answers
            synthesis = f"{data_answer}\n\n---\n\n{comp_answer}"
        return synthesis, ["data_worker", "compliance"], None

    if "compliance" in workers:
        return run_compliance(query), ["compliance"], None
    if "data_worker" in workers:
        return run_data_worker(query), ["data_worker"], None
    if "report_writer" in workers:
        return run_report_writer(query), ["report_writer"], None
    if "email_drafter" in workers:
        preview, email_dict = run_email_drafter(query)
        return preview, ["email_drafter"], email_dict
    if "budget_forecaster" in workers:
        return run_budget_forecaster(query), ["budget_forecaster"], None

    # Generic / off-topic question — answer directly without specialist workers
    try:
        llm = get_llm()
        reply = llm.chat.completions.create(
            model=VENICE_MODEL,
            max_tokens=512,
            messages=[
                {"role": "system", "content": (
                    "You are a friendly assistant for a research scientist managing an ERC grant. "
                    "Answer conversationally and helpfully. If it's a greeting or small talk, "
                    "respond warmly and briefly mention you can help with grants, expenses, "
                    "compliance, and research progress."
                )},
                {"role": "user", "content": query},
            ],
        )
        return reply.choices[0].message.content.strip(), [], None
    except Exception as e:
        return f"⚠️ Could not reach the AI backend: {e}", [], None

# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("Scientist Personal Assistant")
    st.caption("Your AI research & grant management team")

    registry = load_registry()
    workers_info = registry.get("workers", {}) if registry else {}

    st.subheader("Agents")
    _AGENT_INFO = [
        ("📋", "Compliance",     "compliance",    "Checks grant rules, eligibility, and expense policies against ERC documents."),
        ("📊", "Data Worker",    "data_worker",   "Reads the expense spreadsheet — totals, per-category summaries, transaction lookups."),
        ("✏️", "Expense Editor", "data_editor",   "Proposes a new expense row from a natural-language description, then writes it to the sheet after you confirm."),
        ("📝", "Report Writer",  "report_writer", "Drafts progress reports and answers questions about lab notes, GitHub commits, and paper drafts."),
        ("✉️", "Email Drafter",       "email_drafter",     "Writes professional grant-related emails (ERC updates, collaboration requests, recruitment, etc.) and opens them in your mail app."),
        ("📈", "Budget Forecaster",   "budget_forecaster", "Projects remaining budget consumption using spending history, monthly burn rate, and planned-purchase signals from lab notes."),
    ]
    for icon, label, key, desc in _AGENT_INFO:
        sources = workers_info.get(key, {}).get("sources", [])
        with st.expander(f"{icon} {label}"):
            st.caption(desc)
            if sources:
                st.markdown("**Data sources:**")
                for src in sources:
                    src_label = src.get("file") or src.get("repo") or src.get("type") or ""
                    raw       = src.get("summary", "")
                    snippet   = raw.split(".")[0].strip()[:60]
                    if snippet and not snippet.endswith("."):
                        snippet += "."
                    snippet_html = (
                        f"<div style='font-size:0.78rem;color:#9ca3af;margin-top:2px'>{snippet}</div>"
                        if snippet else ""
                    )
                    st.markdown(
                        f"<div style='margin:4px 0 8px 0'>"
                        f"<code style='overflow-wrap:anywhere;word-break:break-word;white-space:pre-wrap'>"
                        f"{src_label}</code>{snippet_html}</div>",
                        unsafe_allow_html=True,
                    )

    if registry:
        st.caption(f"Registry built: {registry.get('generated_at', '?')[:10]}")
    else:
        st.caption("Run `python3 build_registry.py` to index data sources.")

    st.divider()
    st.subheader("Example questions")
    EXAMPLES = [
        "What are the pending action items from emails?",
        "How much did we spend on compute resources?",
        "Is a €6000 spectrograph purchase eligible under ERC rules?",
        "What were the key milestones in Q2 2026?",
        "Write an ERC progress report for Q2 2026",
        "Run an expense audit",
    ]
    for ex in EXAMPLES:
        if st.button(ex, use_container_width=True, key=f"ex_{ex}"):
            st.session_state.pending_input = ex

    if st.button("🗑️ Clear chat", use_container_width=True):
        st.session_state.messages = []
        st.rerun()

# ── Main area ─────────────────────────────────────────────────────────────────

st.title("Scientist Personal Assistant")

if "messages" not in st.session_state:
    st.session_state.messages = []
if "pending_input" not in st.session_state:
    st.session_state.pending_input = None

WORKER_BADGE = {
    "compliance":        "📋 compliance",
    "data_worker":       "📊 data",
    "data_editor":       "✏️ expense editor",
    "report_writer":     "📝 report writer",
    "email_drafter":     "✉️ email drafter",
    "budget_forecaster": "📈 budget forecaster",
}

st.markdown("""
<style>
/* ── Tabs ── */
.stTabs [data-baseweb="tab-list"] { gap: 0; width: 100%; }
.stTabs [data-baseweb="tab"] {
    flex: 1;
    justify-content: center;
    padding: 18px 0;
    border-radius: 6px 6px 0 0;
    /* do NOT set font here — BaseWeb overrides it on children */
}
/* Target only the text span that BaseWeb renders inside the tab button */
.stTabs [data-baseweb="tab"] span[data-testid],
.stTabs [data-baseweb="tab"] > div > p,
.stTabs [data-baseweb="tab"] > div > span,
.stTabs [data-baseweb="tab"] > div {
    font-size: 1.4rem !important;
    font-weight: 700 !important;
    letter-spacing: 0.01em !important;
}
.stTabs [aria-selected="true"] { background-color: #1e3a5f33; }

/* ── Chat input: centered pill, multiple selector fallbacks ─────────── */
[data-testid="stChatInput"] {
    max-width: 780px !important;
    margin-left: auto !important;
    margin-right: auto !important;
    display: block !important;
}
[data-testid="stChatInput"] textarea {
    border-radius: 24px !important;
    padding: 14px 20px !important;
    font-size: 1rem !important;
}
section[data-testid="stBottom"],
div[data-testid="stBottomBlockContainer"] {
    display: flex !important;
    justify-content: center !important;
}
section[data-testid="stBottom"] > div:first-child,
div[data-testid="stBottomBlockContainer"] > div:first-child {
    max-width: 780px !important;
    width: 100% !important;
    padding: 0 16px 12px !important;
}

/* ── Chat bubbles ─────────────────────────────────────── */
.chat-wrap { display:flex; margin: 6px 0 10px; gap: 10px; align-items: flex-start; }
.chat-wrap.user  { flex-direction: row-reverse; }
.chat-avatar {
    width: 38px; height: 38px; border-radius: 50%;
    display: flex; align-items: center; justify-content: center;
    font-size: 1.15rem; flex-shrink: 0; margin-top: 2px;
}
.chat-avatar.user { background: #2563EB; }
.chat-avatar.ai   { background: #374151; }
.chat-bubble {
    padding: 12px 16px;
    border-radius: 18px;
    max-width: 78%;
    font-size: 0.95rem;
    line-height: 1.65;
    word-wrap: break-word;
}
.chat-bubble.user {
    background: #2563EB;
    color: white;
    border-radius: 18px 18px 4px 18px;
}
.chat-bubble.ai {
    background: #1e2130;
    border: 1px solid #2d3348;
    border-radius: 18px 18px 18px 4px;
}
.chat-badge {
    font-size: 0.88rem;
    color: #9ca3af;
    margin-bottom: 7px;
    padding-left: 2px;
    font-weight: 600;
    letter-spacing: 0.01em;
}

/* ── Suppress Streamlit's page-dim animation during script runs ── */
.stApp, .main, [data-testid="stMain"], [data-testid="stAppViewContainer"] {
    transition: none !important;
    animation: none !important;
}
[data-testid="stDecoration"] { display: none !important; }
</style>
""", unsafe_allow_html=True)

# ── Grants helpers (must be defined before any tab block that uses them) ──────

GRANTS_FILE = ROOT / "data" / "grants.json"

WP_STATUS_COLOUR = {
    "completed":   "🟢",
    "in_progress": "🟡",
    "planned":     "⚪",
    "delayed":     "🔴",
}
ROLE_COLOUR = {
    "PI":           "#4A90D9",
    "Postdoc":      "#7B68EE",
    "PhD Student":  "#20B2AA",
    "MSc Student":  "#FFA07A",
    "Research Eng": "#98D8C8",
}


def load_grants() -> list:
    """Return the list of grant dicts from grants.json, or [] if the file is absent."""
    if GRANTS_FILE.exists():
        return json.loads(GRANTS_FILE.read_text(encoding="utf-8")).get("grants", [])
    return []


def save_grants(grants: list) -> None:
    """Persist the grants list back to grants.json (pretty-printed)."""
    GRANTS_FILE.write_text(
        json.dumps({"grants": grants}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def grant_progress(start: str, end: str) -> tuple[float, str]:
    """Compute time elapsed through a grant period.

    Returns:
        fraction  — 0.0 (just started) to 1.0 (ended), clamped
        label     — human-readable time remaining, e.g. "2 yr 3 mo left"
    """
    today = datetime.today()
    s = datetime.strptime(start, "%Y-%m-%d")
    e = datetime.strptime(end,   "%Y-%m-%d")
    total_days   = max((e - s).days, 1)
    elapsed_days = max((today - s).days, 0)
    frac         = min(elapsed_days / total_days, 1.0)
    remaining    = e - today
    if remaining.days <= 0:
        label = "Ended"
    else:
        y, rem = divmod(remaining.days, 365)
        m      = rem // 30
        parts  = []
        if y:
            parts.append(f"{y} yr")
        if m:
            parts.append(f"{m} mo")
        label = " ".join(parts) + " left" if parts else "< 1 mo left"
    return frac, label


PUB_STATUS_CFG = {
    "draft":        ("#888888", "📝 Draft"),
    "submitted":    ("#4A90D9", "📤 Submitted"),
    "under_review": ("#E67E22", "🔍 Under review"),
    "accepted":     ("#20B2AA", "✅ Accepted"),
    "published":    ("#27AE60", "📗 Published"),
}

MS_STATUS_CFG = {
    "planned":     ("#6B7280", "🔵 Planned"),
    "on_track":    ("#27AE60", "🟢 On track"),
    "at_risk":     ("#E67E22", "🟠 At risk"),
    "delayed":     ("#E74C3C", "🔴 Delayed"),
    "completed":   ("#20B2AA", "✅ Completed"),
}

COST_CATEGORIES = ["Personnel", "Equipment", "Travel", "Subcontracting", "Other direct costs", "Indirect costs"]

# ──────────────────────────────────────────────────────────────────────────────

tab_chat, tab_grants, tab_deadlines, tab_apply, tab_team, tab_files = st.tabs([
    "💬  Chat", "🏛️  Grants", "📅  Deadlines", "✍️  Apply", "👥  Team", "📂  Files"
])

# ── Apply tab ─────────────────────────────────────────────────────────────────

GRANT_SECTIONS = {
    "Executive Summary":    ("300",  "Write a compelling executive summary (≈300 words). Convey the vision, key objectives, expected results, and why this team is uniquely positioned to succeed. Make it stand-alone readable."),
    "State of the Art":     ("600",  "Write a state-of-the-art section (≈600 words). Survey the relevant literature, identify the open problem this proposal addresses, and explain clearly what gap remains. Cite the researcher's own prior work where relevant."),
    "Objectives":           ("300",  "Write a concise objectives section (≈300 words). List 3-5 specific, measurable scientific objectives that directly respond to the call. Each objective should be achievable within the grant period."),
    "Methodology":          ("700",  "Write a methodology section (≈700 words). Describe the scientific approach, methods, tools, and experimental design for each objective. Mention the team's existing tools and data where relevant. Address feasibility and risk mitigation."),
    "Work Plan":            ("400",  "Write a work plan section (≈400 words). Break the project into work packages with clear tasks, milestones, and a timeline over the grant period. Mention who leads each work package."),
    "Expected Impact":      ("400",  "Write an expected impact section (≈400 words). Describe the scientific breakthroughs expected, downstream applications, open-science outputs (papers, code, data), and any societal or policy relevance."),
    "Budget Justification": ("350",  "Write a budget justification section (≈350 words). Justify personnel costs, equipment, travel, and any other line items relative to the stated max budget. Be specific about what each expense enables scientifically."),
    "Team & Resources":     ("300",  "Write a team and resources section (≈300 words). Describe the PI's track record, each team member's role and expertise, available infrastructure, and any collaborations or letters of support."),
}

SECTION_ORDER = list(GRANT_SECTIONS.keys())


def _build_apply_context(use_papers: bool, use_lab: bool, use_reports: bool, use_emails: bool) -> str:
    from worker_logic import load_papers, load_lab_notes, load_emails
    parts = []
    if use_papers:
        p = load_papers()
        if p: parts.append(f"=== RESEARCHER'S PAPERS / DRAFTS ===\n{p}")
    if use_lab:
        ln = load_lab_notes()
        if ln: parts.append(f"=== LAB NOTES ===\n{ln}")
    if use_emails:
        em = load_emails()
        if em: parts.append(f"=== EMAIL THREADS ===\n{em}")
    if use_reports:
        rpt_dir = ROOT / "reports"
        if rpt_dir.exists():
            rpts = sorted(rpt_dir.glob("*.md"), reverse=True)[:2]
            for r in rpts:
                parts.append(f"=== PREVIOUS REPORT ({r.name}) ===\n{r.read_text(encoding='utf-8')}")
    return "\n\n".join(parts)


def _generate_section(section: str, call_context: str, research_context: str) -> str:
    _, instruction = GRANT_SECTIONS[section]
    llm = get_llm()
    system = (
        "You are an expert grant writer helping a researcher prepare a competitive proposal. "
        "Write in a clear, confident academic tone. Be specific — reference actual results, "
        "methods, and data from the researcher's work below where relevant. "
        "Do NOT invent facts not present in the provided context.\n\n"
        f"GRANT CALL DETAILS:\n{call_context}\n\n"
        f"RESEARCHER'S BACKGROUND:\n{research_context if research_context else '(none provided)'}"
    )
    result = llm.chat.completions.create(
        model=VENICE_MODEL,
        max_tokens=1200,
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": f"{instruction}\n\nSection to write: {section}"},
        ],
    )
    return result.choices[0].message.content.strip()


with tab_apply:
    if "apply_drafts" not in st.session_state:
        st.session_state.apply_drafts = {}
    if "apply_sel_sections" not in st.session_state:
        st.session_state.apply_sel_sections = {"Executive Summary", "Objectives", "Methodology", "Expected Impact"}

    _SEC_COMMENTS = {
        "Executive Summary":    "Crafting a compelling overview of your research vision…",
        "State of the Art":     "Surveying literature and identifying the open research gap…",
        "Objectives":           "Formulating specific, measurable scientific objectives…",
        "Methodology":          "Designing the scientific approach and experimental pipeline…",
        "Work Plan":            "Structuring work packages, milestones, and timeline…",
        "Expected Impact":      "Articulating scientific breakthroughs and societal relevance…",
        "Budget Justification": "Aligning costs with scientific needs and funder guidelines…",
        "Team & Resources":     "Presenting the team's track record and available infrastructure…",
    }

    # ── Row 1: call details + section/source selector ─────────────────────────
    det_col, sec_col = st.columns([3, 2], gap="large")

    with det_col:
        st.markdown("#### 📋 Call details")
        a1, a2 = st.columns(2)
        apply_funder   = a1.text_input("Funder", placeholder="European Research Council", key="apply_funder")
        apply_call_nm  = a2.text_input("Call / scheme", placeholder="ERC Starting Grant 2027", key="apply_call")
        b1, b2 = st.columns(2)
        apply_deadline = b1.date_input("Deadline", key="apply_deadline")
        apply_budget   = b2.number_input("Max budget (€)", min_value=0, step=50000, key="apply_budget")

        apply_call_text = st.text_area(
            "Call objectives / description",
            key="apply_call_text_val",
            placeholder="Paste the key paragraphs from the call for proposals here…",
            height=130,
        )

        apply_eval_text = st.text_area(
            "Evaluation / submission guidelines",
            key="apply_eval_text_val",
            placeholder="Paste evaluation criteria, scoring rubric, or submission guidelines here…",
            height=110,
        )

    with sec_col:
        st.markdown("#### 📑 Sections")
        ca, cb = st.columns(2)
        if ca.button("All",  use_container_width=True): st.session_state.apply_sel_sections = set(SECTION_ORDER); st.rerun()
        if cb.button("None", use_container_width=True): st.session_state.apply_sel_sections = set(); st.rerun()
        for sec in SECTION_ORDER:
            checked = sec in st.session_state.apply_sel_sections
            if st.checkbox(sec, value=checked, key=f"sec_chk_{sec}"):
                st.session_state.apply_sel_sections.add(sec)
            else:
                st.session_state.apply_sel_sections.discard(sec)

        st.markdown("#### 🗂 Context")
        use_papers  = st.toggle("Papers / Overleaf", value=True,  key="ctx_papers")
        use_lab     = st.toggle("Lab notes",         value=True,  key="ctx_lab")
        use_reports = st.toggle("Previous reports",  value=True,  key="ctx_reports")
        use_emails  = st.toggle("Email threads",     value=False, key="ctx_emails")

    # ── Generate button — full width, outside columns ─────────────────────────
    st.divider()
    gen_btn = st.button(
        f"⚡ Generate {len(st.session_state.apply_sel_sections)} section(s)",
        type="primary", use_container_width=True,
        disabled=not st.session_state.apply_sel_sections,
    )

    _eval_block = ""
    if st.session_state.get("apply_eval_text_val", "").strip():
        _eval_block = f"\n\nEVALUATION / SUBMISSION GUIDELINES:\n{st.session_state['apply_eval_text_val']}"

    call_ctx = (
        f"Funder: {st.session_state.get('apply_funder','')}\n"
        f"Call: {st.session_state.get('apply_call','')}\n"
        f"Deadline: {st.session_state.get('apply_deadline','')}\n"
        f"Max budget: €{st.session_state.get('apply_budget', 0):,}\n\n"
        f"CALL OBJECTIVES / DESCRIPTION:\n{st.session_state.get('apply_call_text_val','')}"
        f"{_eval_block}"
    )

    # ── Generation — full width so st.status streams properly ─────────────────
    if gen_btn:
        sections_to_gen = [s for s in SECTION_ORDER if s in st.session_state.apply_sel_sections]
        with st.status("✍️ Drafting your grant proposal…", expanded=True) as gen_status:
            st.write("🗂 Loading your research context…")
            try:
                research_ctx = _build_apply_context(use_papers, use_lab, use_reports, use_emails)
                src_lines = []
                if use_papers and (ROOT / "papers").exists():
                    n = len(list((ROOT / "papers").glob("*.tex")))
                    if n: src_lines.append(f"{n} paper draft(s)")
                if use_lab:     src_lines.append("lab notes")
                if use_reports: src_lines.append("previous reports")
                if use_emails:  src_lines.append("email threads")
                st.write(f"   ✅ Context loaded: {', '.join(src_lines) or 'none'}")
            except Exception as e:
                st.write(f"   ⚠️ Context load failed ({e}), continuing without background…")
                research_ctx = ""

            st.write(f"   → Writing {len(sections_to_gen)} section(s) — each takes ~15 s")

            for i, sec in enumerate(sections_to_gen):
                st.write(f"**{i+1}/{len(sections_to_gen)} — {sec}**  _{_SEC_COMMENTS.get(sec,'')}_")
                try:
                    draft = _generate_section(sec, call_ctx, research_ctx)
                    st.session_state.apply_drafts[sec] = draft
                    st.write(f"   ✅ {len(draft.split())} words")
                except Exception as e:
                    st.session_state.apply_drafts[sec] = f"[Error: {e}]"
                    st.write(f"   ❌ {e}")

            gen_status.update(
                label=f"✅ {len(sections_to_gen)} section(s) ready — see drafts below",
                state="complete", expanded=False,
            )

    # ── Drafts ────────────────────────────────────────────────────────────────
    drafts = st.session_state.apply_drafts
    if not drafts:
        if not gen_btn:
            st.info("Fill in the call details above, pick sections, and click **Generate**.")
    else:
        st.divider()
        dc1, dc2 = st.columns([1, 1])
        if dc1.button("💾 Save full draft as .md", type="primary"):
            drafts_dir = ROOT / "grant_drafts"
            drafts_dir.mkdir(exist_ok=True)
            ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
            out = drafts_dir / f"draft_{ts}.md"
            lines = [
                f"# {st.session_state.get('apply_call','Grant Application')}\n",
                f"**Funder:** {st.session_state.get('apply_funder','')}  "
                f"**Deadline:** {st.session_state.get('apply_deadline','')}  "
                f"**Budget:** €{st.session_state.get('apply_budget',0):,}\n\n---\n",
            ]
            for sec in SECTION_ORDER:
                if sec in drafts:
                    lines.append(f"\n## {sec}\n\n{drafts[sec]}\n")
            out.write_text("\n".join(lines), encoding="utf-8")
            st.success(f"Saved to `grant_drafts/{out.name}`")
        if dc2.button("🗑 Clear all drafts"):
            st.session_state.apply_drafts = {}
            st.rerun()

        for sec in SECTION_ORDER:
            if sec not in drafts:
                continue
            sh1, sh2 = st.columns([5, 1])
            sh1.markdown(f"### {sec}")
            if sh2.button("↺ Redo", key=f"regen_{sec}"):
                with st.status(f"Rewriting {sec}…", expanded=True) as rs:
                    try:
                        rc = _build_apply_context(use_papers, use_lab, use_reports, use_emails)
                        st.session_state.apply_drafts[sec] = _generate_section(sec, call_ctx, rc)
                        rs.update(label="✅ Done", state="complete", expanded=False)
                    except Exception as e:
                        st.session_state.apply_drafts[sec] = f"[Error: {e}]"
                        rs.update(label=f"❌ {e}", state="error", expanded=False)
                st.rerun()

            updated = st.text_area(
                sec, value=drafts[sec], height=260,
                key=f"draft_area_{sec}", label_visibility="collapsed",
            )
            if updated != drafts[sec]:
                st.session_state.apply_drafts[sec] = updated
            st.markdown("---")


# ── Team tab ──────────────────────────────────────────────────────────────────
with tab_team:
    _CONTRACT_DAYS_WARN  = 90   # orange warning threshold
    _CONTRACT_DAYS_ALERT = 30   # red alert threshold

    def _contract_urgency(end_date: str) -> tuple[str, str]:
        """Return (colour, label) for a contract end date string."""
        try:
            d     = datetime.strptime(end_date, "%Y-%m-%d").date()
            delta = (d - datetime.today().date()).days
        except (ValueError, TypeError):
            return "#888", "No end date"
        if delta < 0:
            return "#E74C3C", f"Expired {abs(delta)}d ago"
        if delta <= _CONTRACT_DAYS_ALERT:
            return "#E74C3C", f"Expires in {delta}d"
        if delta <= _CONTRACT_DAYS_WARN:
            return "#E67E22", f"Expires in {delta}d"
        return "#27AE60", f"Until {end_date}"

    grants     = load_grants()
    all_members = [
        {**m, "_grant_id": g["id"], "_grant_title": g["title"]}
        for g in grants for m in g.get("team", [])
    ]

    # ── Top metrics ──
    if all_members:
        total_salary = sum(m.get("annual_salary_eur", 0) * m.get("fte", 1.0) for m in all_members)
        expiring_soon = sum(
            1 for m in all_members
            if m.get("contract_end") and
               0 <= (datetime.strptime(m["contract_end"], "%Y-%m-%d").date() - datetime.today().date()).days <= _CONTRACT_DAYS_WARN
        )
        mc1, mc2, mc3 = st.columns(3)
        mc1.metric("Team members",           len(all_members))
        mc2.metric("Contracts expiring <90d", expiring_soon)
        mc3.metric("Annual salary cost",     f"€{total_salary:,.0f}")
        st.divider()

    # ── Filter ──
    grant_options = ["All grants"] + [g["title"] for g in grants]
    sel_grant     = st.selectbox("Filter by grant", grant_options, key="team_filter_grant")
    visible = all_members if sel_grant == "All grants" else [
        m for m in all_members if m["_grant_title"] == sel_grant
    ]

    if not visible:
        st.info("No team members found.")
    else:
        for m in visible:
            c_colour, c_label = _contract_urgency(m.get("contract_end", ""))
            with st.container(border=True):
                col_name, col_role, col_contract, col_fte, col_salary = st.columns([3, 2, 2, 1, 2])
                role_c = ROLE_COLOUR.get(m["role"], "#888")
                col_name.markdown(
                    f"**{m['name']}**"
                    + (f"<br><span style='color:#888;font-size:0.8em'>{m.get('email','')}</span>" if m.get("email") else ""),
                    unsafe_allow_html=True,
                )
                col_role.markdown(
                    f"<span style='background:{role_c}22;color:{role_c};border:1px solid {role_c};"
                    f"padding:2px 8px;border-radius:12px;font-size:0.85em'>{m['role']}</span>",
                    unsafe_allow_html=True,
                )
                col_contract.markdown(
                    f"<span style='color:{c_colour};font-size:0.85em'>⏱ {c_label}</span>",
                    unsafe_allow_html=True,
                )
                col_fte.metric("FTE", f"{m.get('fte', 1.0):.1f}")
                col_salary.metric("Salary/yr", f"€{m.get('annual_salary_eur', 0):,.0f}")

                # Institution + grant badge
                st.caption(
                    (f"{m.get('institution', '')}  ·  " if m.get("institution") else "")
                    + f"Grant: `{m['_grant_id']}`  ·  Joined: {m.get('joined', '—')}"
                )

                # Inline edit
                with st.expander("Edit", expanded=False):
                    with st.form(f"edit_member_{m['name'].replace(' ','_')}_{m['_grant_id']}"):
                        ef1, ef2 = st.columns(2)
                        new_role     = ef1.text_input("Role",           value=m.get("role", ""),     key=f"er_{m['name']}_{m['_grant_id']}")
                        new_email    = ef2.text_input("Email",          value=m.get("email", ""),    key=f"ee_{m['name']}_{m['_grant_id']}")
                        ef3, ef4, ef5 = st.columns(3)
                        new_contract = ef3.text_input("Contract end (YYYY-MM-DD)", value=m.get("contract_end", ""), key=f"ec_{m['name']}_{m['_grant_id']}")
                        new_fte      = ef4.number_input("FTE",          value=float(m.get("fte", 1.0)),  min_value=0.0, max_value=1.0, step=0.1, key=f"ef_{m['name']}_{m['_grant_id']}")
                        new_salary   = ef5.number_input("Annual salary (€)", value=int(m.get("annual_salary_eur", 0)), min_value=0, step=1000, key=f"es_{m['name']}_{m['_grant_id']}")
                        new_inst     = st.text_input("Institution",     value=m.get("institution", ""), key=f"ei_{m['name']}_{m['_grant_id']}")
                        if st.form_submit_button("Save", type="primary"):
                            all_g = load_grants()
                            for ag in all_g:
                                if ag["id"] == m["_grant_id"]:
                                    for tm in ag.get("team", []):
                                        if tm["name"] == m["name"]:
                                            tm.update({
                                                "role": new_role, "email": new_email,
                                                "contract_end": new_contract, "fte": new_fte,
                                                "annual_salary_eur": new_salary, "institution": new_inst,
                                            })
                            save_grants(all_g)
                            st.success("Saved.")
                            st.rerun()

    # ── Add member ──
    st.divider()
    with st.expander("➕ Add team member"):
        grant_ids = [g["id"] for g in grants]
        with st.form("add_team_member"):
            af1, af2 = st.columns(2)
            new_name  = af1.text_input("Name")
            new_role  = af2.selectbox("Role", ["Postdoc", "PhD Student", "MSc Student", "Research Engineer", "PI", "Collaborator"])
            af3, af4  = st.columns(2)
            new_grant = af3.selectbox("Grant", grant_ids)
            new_email = af4.text_input("Email")
            af5, af6, af7 = st.columns(3)
            new_joined   = af5.text_input("Joined (YYYY-MM-DD)",       value=datetime.today().strftime("%Y-%m-%d"))
            new_contract = af6.text_input("Contract end (YYYY-MM-DD)", value="")
            new_fte      = af7.number_input("FTE", value=1.0, min_value=0.0, max_value=1.0, step=0.1)
            af8, af9 = st.columns(2)
            new_salary = af8.number_input("Annual salary (€)", min_value=0, step=1000, value=0)
            new_inst   = af9.text_input("Institution")
            if st.form_submit_button("Add member", type="primary"):
                if new_name:
                    all_g = load_grants()
                    for ag in all_g:
                        if ag["id"] == new_grant:
                            ag.setdefault("team", []).append({
                                "name": new_name, "role": new_role,
                                "joined": new_joined, "contract_end": new_contract,
                                "fte": new_fte, "annual_salary_eur": new_salary,
                                "email": new_email, "institution": new_inst,
                            })
                    save_grants(all_g)
                    st.success(f"{new_name} added.")
                    st.rerun()


# ── Files tab ─────────────────────────────────────────────────────────────────
with tab_files:
    st.subheader("Drop files to update data sources")
    st.caption(
        "Files are saved to the appropriate directory, PDFs are added to the compliance "
        "vector database, then the worker registry is rebuilt automatically."
    )

    category = st.selectbox(
        "File category",
        list(DEST_DIRS.keys()),
        help="Choose the type of file you're uploading so it goes to the right place.",
    )

    accepted_ext = ACCEPTED_EXTENSIONS[category]
    uploaded_files = st.file_uploader(
        f"Drop {category} files here",
        type=[e.lstrip(".") for e in accepted_ext],
        accept_multiple_files=True,
        label_visibility="collapsed",
    )

    if uploaded_files:
        if st.button("💾 Save & update registry", type="primary"):
            saved = []
            errors = []
            prog = st.progress(0, text="Saving files…")
            for i, f in enumerate(uploaded_files):
                try:
                    dest = save_uploaded_file(f, category)
                    saved.append(dest.name)
                    if category == "Policy document (PDF)":
                        prog.progress((i + 0.5) / len(uploaded_files), text=f"Ingesting {f.name} into vector DB…")
                        n_chunks = _ingest_pdf_to_chroma(dest)
                        saved[-1] += f" ({n_chunks} chunks → ChromaDB)"
                    get_vector_db.clear()   # force reload on next compliance query
                except Exception as e:
                    errors.append(f"{f.name}: {e}")
                prog.progress((i + 1) / len(uploaded_files), text=f"Saved {f.name}")

            prog.progress(1.0, text="Rebuilding registry…")
            try:
                rebuild_registry_ui()
                prog.empty()
                st.success("Done! Files saved and registry updated.")
                for s in saved:
                    st.markdown(f"- ✅ {s}")
            except Exception as e:
                prog.empty()
                st.error(f"Registry rebuild failed: {e}")
            for err in errors:
                st.error(err)

    st.divider()
    st.subheader("📄 Import from Overleaf")
    st.caption("Export your project in Overleaf via **Menu → Download → Source (.zip)**, then drop the file here.")
    overleaf_zip = st.file_uploader(
        "Overleaf ZIP", type=["zip"], key="overleaf_zip", label_visibility="collapsed"
    )
    if overleaf_zip:
        if st.button("📦 Extract & update registry", type="primary", key="extract_zip"):
            with st.spinner("Extracting .tex files…"):
                try:
                    names, count = _extract_overleaf_zip(overleaf_zip)
                    if count == 0:
                        st.warning("No .tex or .bib files found in the ZIP.")
                    else:
                        st.success(f"Extracted {count} file(s) to `papers/`")
                        for n in names:
                            st.markdown(f"- ✅ {n}")
                        rebuild_registry_ui()
                        st.info("Registry rebuilt — papers are now available to the report writer.")
                except Exception as e:
                    st.error(f"Extraction failed: {e}")

    st.divider()
    st.subheader("Current files by category")
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**📋 Knowledge base** (`knowledge_base/`)")
        for f in sorted((ROOT / "knowledge_base").iterdir()):
            st.caption(f"• {f.name}")
        st.markdown("**📝 Lab notes** (`lab_notes/`)")
        lab_dir = ROOT / "lab_notes"
        if lab_dir.exists():
            for f in sorted(lab_dir.iterdir()):
                st.caption(f"• {f.name}")
        st.markdown("**📄 Papers / Overleaf** (`papers/`)")
        papers_dir2 = ROOT / "papers"
        if papers_dir2.exists():
            tex_files = [f for f in sorted(papers_dir2.iterdir()) if f.suffix in {".tex", ".bib"}]
            if tex_files:
                for f in tex_files:
                    st.caption(f"• {f.name}")
            else:
                st.caption("_(empty — drop an Overleaf ZIP above)_")
        else:
            st.caption("_(no papers/ directory yet)_")
    with col2:
        st.markdown("**✉️ Email threads** (`emails/`)")
        email_dir = ROOT / "emails"
        if email_dir.exists():
            for f in sorted(email_dir.iterdir()):
                st.caption(f"• {f.name}")
        st.markdown("**📄 Generated reports** (`reports/`)")
        reports_dir = ROOT / "reports"
        if reports_dir.exists():
            for f in sorted(reports_dir.iterdir(), reverse=True)[:5]:
                st.caption(f"• {f.name}")

    # ── Spreadsheet editor ───────────────────────────────────────────────────
    st.divider()
    st.subheader("✏️ Edit expense spreadsheet")
    xlsx_files = sorted((ROOT / "knowledge_base").glob("*.xlsx"))
    if not xlsx_files:
        st.caption("No .xlsx files found in `knowledge_base/`.")
    else:
        import pandas as pd
        sel_xlsx = st.selectbox(
            "File", [f.name for f in xlsx_files], label_visibility="collapsed"
        )
        xlsx_path = ROOT / "knowledge_base" / sel_xlsx
        if "xlsx_edit_df" not in st.session_state or st.session_state.get("xlsx_edit_file") != sel_xlsx:
            st.session_state.xlsx_edit_df   = pd.read_excel(xlsx_path)
            st.session_state.xlsx_edit_file = sel_xlsx

        edited_df = st.data_editor(
            st.session_state.xlsx_edit_df,
            num_rows="dynamic",
            use_container_width=True,
            key=f"data_editor_{sel_xlsx}",
        )
        sc1, sc2 = st.columns([1, 4])
        if sc1.button("💾 Save to file", type="primary"):
            edited_df.to_excel(xlsx_path, index=False)
            st.session_state.xlsx_edit_df = edited_df
            rebuild_registry_ui()
            sc2.success("Saved and registry rebuilt.")
        if sc1.button("↺ Reload from file"):
            st.session_state.xlsx_edit_df = pd.read_excel(xlsx_path)
            st.rerun()


# ── Grants tab ────────────────────────────────────────────────────────────────


def _budget_chart(grant: dict):
    """Render a Plotly budget chart for a grant inside the current Streamlit context.

    Shows:
      - Monthly spend bars (grouped by Category from the expense Excel)
      - Cumulative spend line
      - Linear projection line through the grant end date
      - Four st.metric cards: Spent, Remaining, Monthly rate, Projected total

    Reads from knowledge_base/erc_solar_physics_expenses.xlsx.  Displays an
    info message if the file is absent rather than raising an error.
    """
    import pandas as pd
    import plotly.graph_objects as go

    budget = grant.get("total_budget_eur", 0)
    start  = datetime.strptime(grant["start_date"], "%Y-%m-%d")
    end    = datetime.strptime(grant["end_date"],   "%Y-%m-%d")

    xlsx_files = list((ROOT / "knowledge_base").glob("*.xlsx"))
    if not xlsx_files:
        st.caption("No expense data found in `knowledge_base/`.")
        return

    df = pd.read_excel(xlsx_files[0])
    date_col   = next((c for c in df.columns if "date" in c.lower()), None)
    amount_col = next((c for c in df.columns if "amount" in c.lower()), None)
    if not date_col or not amount_col:
        st.caption("Expense file missing Date or Amount column.")
        return

    df[date_col]   = pd.to_datetime(df[date_col], errors="coerce")
    df[amount_col] = pd.to_numeric(df[amount_col], errors="coerce").fillna(0)
    df = df.dropna(subset=[date_col])

    df["month"] = df[date_col].dt.to_period("M").astype(str)
    monthly = df.groupby("month")[amount_col].sum().reset_index()
    monthly.columns = ["month", "spent"]
    monthly = monthly.sort_values("month")
    monthly["cumulative"] = monthly["spent"].cumsum()

    # Projected: linear extrapolation from current cumulative spend rate
    today = datetime.today()
    total_months = max((end.year - start.year) * 12 + (end.month - start.month), 1)
    elapsed_months = max((today.year - start.year) * 12 + (today.month - start.month), 1)
    spent_so_far = monthly["spent"].sum()
    monthly_rate = spent_so_far / elapsed_months if elapsed_months else 0

    fig = go.Figure()
    fig.add_bar(x=monthly["month"], y=monthly["spent"],
                name="Monthly spend", marker_color="#4A90D9", opacity=0.75)
    fig.add_scatter(x=monthly["month"], y=monthly["cumulative"],
                    name="Cumulative", mode="lines+markers",
                    line=dict(color="#E67E22", width=2))

    # Project cumulative forward to grant end
    last_month = monthly["month"].iloc[-1] if not monthly.empty else start.strftime("%Y-%m")
    last_cum   = monthly["cumulative"].iloc[-1] if not monthly.empty else 0
    proj_months, proj_vals = [last_month], [last_cum]
    cur = pd.Period(last_month, "M") + 1
    end_period = pd.Period(end.strftime("%Y-%m"), "M")
    while cur <= end_period:
        proj_months.append(str(cur))
        proj_vals.append(last_cum + monthly_rate * (len(proj_months) - 1))
        cur += 1
    if len(proj_months) > 1:
        fig.add_scatter(x=proj_months, y=proj_vals,
                        name="Projected", mode="lines",
                        line=dict(color="#27AE60", dash="dash", width=2))

    fig.add_hline(y=budget, line_dash="dot", line_color="red",
                  annotation_text=f"Budget €{budget:,.0f}", annotation_position="top left")

    remaining    = budget - spent_so_far
    pct_spent    = spent_so_far / budget * 100 if budget else 0
    months_left  = max((end.year - today.year) * 12 + (end.month - today.month), 0)
    projected_total = spent_so_far + monthly_rate * months_left

    # Stats row above the chart
    mc1, mc2, mc3, mc4 = st.columns(4)
    mc1.metric("Spent",        f"€{spent_so_far:,.0f}", f"{pct_spent:.1f}% of budget")
    mc2.metric("Remaining",    f"€{remaining:,.0f}")
    mc3.metric("Monthly rate", f"€{monthly_rate:,.0f}/mo")
    proj_delta = projected_total - budget
    mc4.metric(
        "Projected total",
        f"€{projected_total:,.0f}",
        delta=f"{'over' if proj_delta > 0 else 'under'} by €{abs(proj_delta):,.0f}",
        delta_color="inverse",
    )

    fig.update_layout(
        height=280,
        margin=dict(l=0, r=10, t=10, b=0),
        legend=dict(
            orientation="h",
            yanchor="bottom", y=-0.25,
            xanchor="left",   x=0,
            font=dict(size=12),
        ),
        xaxis_title=None,
        yaxis_title="EUR",
        yaxis=dict(tickprefix="€", tickformat=",.0f"),
    )
    st.plotly_chart(fig, use_container_width=True)


with tab_grants:
    grants = load_grants()

    # ── Top metrics ──
    if grants:
        active  = [g for g in grants if g.get("status") == "active"]
        total_b = sum(g.get("total_budget_eur", 0) for g in active)
        members = {m["name"] for g in active for m in g.get("team", [])}
        c1, c2, c3 = st.columns(3)
        c1.metric("Active grants",     len(active))
        c2.metric("Total budget",      f"€{total_b:,.0f}")
        c3.metric("Team members",      len(members))
        st.divider()

    # ── Grant cards ──
    if not grants:
        st.info("No grants yet. Add one below.")
    for g in grants:
        gid  = g["id"]
        frac, time_label = grant_progress(g["start_date"], g["end_date"])
        pct = int(frac * 100)

        with st.container(border=True):
            hcol, bcol = st.columns([3, 1])
            with hcol:
                status_icon = "🟢" if g.get("status") == "active" else "⚫"
                st.markdown(f"### {status_icon} {g['title']}")
                st.caption(
                    f"**{g['funder']}** · {g['type']} · "
                    f"PI: {g['pi']} · ID: `{g['id']}`"
                )
            with bcol:
                st.metric("Budget", f"€{g.get('total_budget_eur', 0):,.0f}")

            # Progress bar
            bar_col, lbl_col = st.columns([5, 1])
            with bar_col:
                st.progress(frac, text=f"{g['start_date']}  →  {g['end_date']}")
            with lbl_col:
                colour = "red" if frac > 0.85 else "orange" if frac > 0.6 else "green"
                st.markdown(
                    f"<span style='color:{colour};font-weight:bold'>{time_label}</span>  "
                    f"<span style='color:grey;font-size:0.85em'>({pct}% elapsed)</span>",
                    unsafe_allow_html=True,
                )

            # Team & work packages side by side
            tcol, wpcol = st.columns(2)
            with tcol:
                st.markdown("**Team**")
                for m in g.get("team", []):
                    rc = ROLE_COLOUR.get(m["role"], "#888")
                    joined = m.get("joined", "")
                    st.markdown(
                        f"<span style='background:{rc}22;border-left:3px solid {rc};"
                        f"padding:2px 8px;border-radius:3px;font-size:0.9em'>"
                        f"**{m['name']}** — {m['role']}"
                        f"</span>"
                        + (f" <span style='color:grey;font-size:0.8em'>since {joined}</span>" if joined else ""),
                        unsafe_allow_html=True,
                    )

            with wpcol:
                st.markdown("**Work packages**")
                for wp in g.get("work_packages", []):
                    icon = WP_STATUS_COLOUR.get(wp.get("status", "planned"), "⚪")
                    st.markdown(f"{icon} `{wp['id']}` {wp['title']}")

            st.markdown("---")

            # ── Budget burn rate ──
            with st.expander("📊 Budget burn rate"):
                _budget_chart(g)

            # ── Milestone tracker ──
            milestones = g.get("milestones", [])
            done_count = sum(1 for m in milestones if m.get("status") == "completed")
            with st.expander(f"🏁 Milestones ({done_count}/{len(milestones)} completed)"):
                if milestones:
                    today_str = datetime.today().strftime("%Y-%m-%d")
                    for mi, ms in enumerate(milestones):
                        sc, slabel = MS_STATUS_CFG.get(ms.get("status", "planned"), ("#888", "🔵 Planned"))
                        due        = ms.get("due_date", "")
                        overdue    = due and due < today_str and ms.get("status") not in ("completed",)
                        mc1, mc2, mc3 = st.columns([4, 2, 2])
                        with mc1:
                            st.markdown(
                                f"**{ms.get('id','M?')}** — {ms['title']}"
                                + (f" <span style='color:#E74C3C;font-size:0.8em'>⚠ overdue</span>" if overdue else ""),
                                unsafe_allow_html=True,
                            )
                            if ms.get("description"):
                                st.caption(ms["description"])
                            if ms.get("work_package"):
                                st.caption(f"WP: {ms['work_package']}")
                        with mc2:
                            st.markdown(
                                f"<span style='background:{sc}22;color:{sc};border:1px solid {sc};"
                                f"padding:2px 8px;border-radius:12px;font-size:0.8em'>{slabel}</span>",
                                unsafe_allow_html=True,
                            )
                        with mc3:
                            st.caption(f"Due: {due}" if due else "No due date")
                        # Inline status updater
                        new_status = st.selectbox(
                            "Update status",
                            list(MS_STATUS_CFG.keys()),
                            index=list(MS_STATUS_CFG.keys()).index(ms.get("status", "planned")),
                            key=f"ms_status_{gid}_{mi}",
                            label_visibility="collapsed",
                        )
                        if new_status != ms.get("status"):
                            all_g = load_grants()
                            for ag in all_g:
                                if ag["id"] == gid:
                                    ag.setdefault("milestones", [])[mi]["status"] = new_status
                            save_grants(all_g)
                            st.rerun()
                        st.markdown("<hr style='margin:6px 0;opacity:0.15'>", unsafe_allow_html=True)
                else:
                    st.info("No milestones yet. Add one below.")

                # Add milestone form
                with st.form(f"add_ms_{gid}"):
                    st.markdown("**Add milestone**")
                    m1, m2 = st.columns(2)
                    ms_id    = m1.text_input("ID",    placeholder="M1.1", key=f"msid_{gid}")
                    ms_wp    = m2.text_input("Work package", placeholder="WP2", key=f"mswp_{gid}")
                    ms_title = st.text_input("Title", placeholder="First simulation results delivered", key=f"mst_{gid}")
                    ms_desc  = st.text_area("Description (optional)", height=60, key=f"msd_{gid}")
                    m3, m4 = st.columns(2)
                    ms_due    = m3.date_input("Due date", key=f"msdue_{gid}")
                    ms_status = m4.selectbox("Status", list(MS_STATUS_CFG.keys()), key=f"msst_{gid}")
                    if st.form_submit_button("Add milestone", type="primary"):
                        if ms_title:
                            all_g = load_grants()
                            for ag in all_g:
                                if ag["id"] == gid:
                                    ag.setdefault("milestones", []).append({
                                        "id":           ms_id,
                                        "title":        ms_title,
                                        "description":  ms_desc,
                                        "work_package": ms_wp,
                                        "due_date":     str(ms_due),
                                        "status":       ms_status,
                                    })
                            save_grants(all_g)
                            st.success("Milestone added.")
                            st.rerun()

            # ── Budget planner ──
            with st.expander("🧮 Budget planner"):
                total_budget = g.get("total_budget_eur", 0)
                xls_path = ROOT / "knowledge_base" / "erc_solar_physics_expenses.xlsx"
                spent_so_far = 0.0
                if xls_path.exists():
                    import pandas as _pd
                    try:
                        _df = _pd.read_excel(xls_path)
                        if "Amount_EUR" in _df.columns:
                            spent_so_far = float(_df["Amount_EUR"].sum())
                    except Exception:
                        pass
                remaining_budget = total_budget - spent_so_far

                st.markdown(
                    f"**Remaining budget:** €{remaining_budget:,.0f} of €{total_budget:,.0f} total"
                    f"  ·  **Spent so far:** €{spent_so_far:,.0f}",
                )
                st.markdown("#### What-if scenario")
                bp1, bp2, bp3 = st.columns(3)
                bp_cat      = bp1.selectbox("Category", COST_CATEGORIES, key=f"bp_cat_{gid}")
                bp_amount   = bp2.number_input("Annual cost (€)", min_value=0, step=1000,
                                               value=65000, key=f"bp_amt_{gid}")
                bp_months   = bp3.number_input("Duration (months)", min_value=1, max_value=60,
                                               value=18, key=f"bp_dur_{gid}")
                bp_label    = st.text_input("Description (optional)",
                                            placeholder="e.g. New postdoc — MHD simulations",
                                            key=f"bp_lbl_{gid}")

                if st.button("Calculate", key=f"bp_calc_{gid}", type="primary"):
                    total_cost   = bp_amount * bp_months / 12
                    new_remaining = remaining_budget - total_cost
                    affordable   = new_remaining >= 0

                    r1, r2, r3 = st.columns(3)
                    r1.metric("Total cost",      f"€{total_cost:,.0f}")
                    r2.metric("After commitment", f"€{new_remaining:,.0f}",
                              delta=f"-€{total_cost:,.0f}",
                              delta_color="inverse")
                    r3.metric("Feasible?", "✅ Yes" if affordable else "❌ No")

                    if affordable:
                        pct_used = (total_cost / remaining_budget) * 100
                        st.success(
                            f"This commitment uses **{pct_used:.1f}%** of remaining budget. "
                            f"€{new_remaining:,.0f} would remain."
                        )
                    else:
                        shortfall = abs(new_remaining)
                        st.error(
                            f"Budget shortfall of **€{shortfall:,.0f}**. "
                            f"Consider reducing duration to "
                            f"**{int(remaining_budget / bp_amount * 12)} months** or less."
                        )

                    # LLM narrative
                    with st.spinner("Getting assessment…"):
                        try:
                            _llm = get_llm()
                            narrative = _llm.chat.completions.create(
                                model=VENICE_MODEL,
                                max_tokens=220,
                                messages=[{"role": "system", "content": (
                                    "You are a grant management advisor for ERC grants. "
                                    "Give a concise 2-3 sentence assessment of the budget scenario. "
                                    "Mention ERC-specific considerations if relevant (e.g. personnel "
                                    "cost rules, indirect cost caps). Be direct and practical."
                                )}, {"role": "user", "content": (
                                    f"Grant: {g['title']} ({g['type']})\n"
                                    f"Total budget: €{total_budget:,.0f}\n"
                                    f"Spent so far: €{spent_so_far:,.0f}\n"
                                    f"Remaining: €{remaining_budget:,.0f}\n"
                                    f"Proposed: {bp_label or bp_cat} — €{bp_amount:,.0f}/year "
                                    f"for {bp_months} months = €{total_cost:,.0f} total\n"
                                    f"Feasible: {'yes' if affordable else 'no'}"
                                )}],
                            ).choices[0].message.content.strip()
                            st.info(narrative)
                        except Exception:
                            pass

            # ── Publications ──
            pubs = g.get("publications", [])
            with st.expander(f"📚 Publications ({len(pubs)})"):
                if pubs:
                    for pi, pub in enumerate(pubs):
                        st_col, badge_col = st.columns([5, 1])
                        sc, slabel = PUB_STATUS_CFG.get(pub.get("status", "draft"), ("#888", "📝 Draft"))
                        with st_col:
                            st.markdown(f"**{pub['title']}**")
                            meta = []
                            if pub.get("authors"): meta.append(pub["authors"])
                            if pub.get("journal"): meta.append(f"*{pub['journal']}*")
                            if pub.get("year"):    meta.append(str(pub["year"]))
                            if pub.get("doi"):     meta.append(f"[DOI]({pub['doi']})")
                            if meta:
                                st.caption(" · ".join(meta))
                        with badge_col:
                            st.markdown(
                                f"<span style='background:{sc}22;color:{sc};border:1px solid {sc};"
                                f"padding:2px 8px;border-radius:12px;font-size:0.8em'>{slabel}</span>",
                                unsafe_allow_html=True,
                            )

                # Add publication form
                with st.form(f"add_pub_{gid}"):
                    st.markdown("**Add publication**")
                    p1, p2 = st.columns(2)
                    pub_title   = p1.text_input("Title",   key=f"pt_{gid}")
                    pub_authors = p2.text_input("Authors", key=f"pa_{gid}", placeholder="Vega M., Asante K., ...")
                    p3, p4, p5 = st.columns(3)
                    pub_journal = p3.text_input("Journal / venue", key=f"pj_{gid}")
                    pub_year    = p4.number_input("Year", min_value=2000, max_value=2040,
                                                  value=datetime.today().year, key=f"py_{gid}")
                    pub_status  = p5.selectbox("Status",
                                               ["draft", "submitted", "under_review", "accepted", "published"],
                                               key=f"ps_{gid}")
                    pub_doi = st.text_input("DOI (optional)", key=f"pd_{gid}", placeholder="10.xxxx/xxxxx")
                    if st.form_submit_button("Add", type="primary"):
                        if pub_title:
                            all_g = load_grants()
                            for ag in all_g:
                                if ag["id"] == gid:
                                    ag.setdefault("publications", []).append({
                                        "title":   pub_title,
                                        "authors": pub_authors,
                                        "journal": pub_journal,
                                        "year":    int(pub_year),
                                        "status":  pub_status,
                                        "doi":     pub_doi,
                                    })
                            save_grants(all_g)
                            st.success("Publication added.")
                            st.rerun()

            # ── Reports ──
            reports_dir = ROOT / "reports"
            if reports_dir.exists():
                rpts = sorted(reports_dir.glob("*.md"), reverse=True)
                if rpts:
                    with st.expander(f"📄 Generated reports ({len(rpts)})"):
                        for r in rpts:
                            ts = r.stem.replace("grant_report_", "")
                            try:
                                dt = datetime.strptime(ts, "%Y%m%d_%H%M%S").strftime("%d %b %Y %H:%M")
                            except ValueError:
                                dt = ts
                            col_a, col_b, col_c = st.columns([4, 1, 1])
                            col_a.markdown(f"📄 {dt}")
                            if col_b.button("View", key=f"view_{r.name}"):
                                st.session_state[f"report_mode_{r.name}"] = "view"
                            if col_c.button("Edit", key=f"edit_{r.name}"):
                                st.session_state[f"report_mode_{r.name}"] = "edit"
                                st.session_state[f"report_text_{r.name}"] = r.read_text(encoding="utf-8")

                        for r in rpts:
                            mode = st.session_state.get(f"report_mode_{r.name}")
                            if mode == "view":
                                st.markdown("---")
                                st.markdown(r.read_text(encoding="utf-8"))
                                if st.button("Close", key=f"close_{r.name}"):
                                    del st.session_state[f"report_mode_{r.name}"]
                                    st.rerun()
                            elif mode == "edit":
                                st.markdown("---")
                                edited = st.text_area(
                                    "Edit report",
                                    value=st.session_state.get(f"report_text_{r.name}", ""),
                                    height=450,
                                    key=f"rpt_area_{r.name}",
                                    label_visibility="collapsed",
                                )
                                sa, sb = st.columns([1, 5])
                                if sa.button("💾 Save", key=f"save_{r.name}", type="primary"):
                                    r.write_text(edited, encoding="utf-8")
                                    st.session_state[f"report_text_{r.name}"] = edited
                                    sb.success("Saved.")
                                if sa.button("Close", key=f"closeedit_{r.name}"):
                                    del st.session_state[f"report_mode_{r.name}"]
                                    st.rerun()

    # ── Add / edit grant form ──
    st.divider()
    with st.expander("➕ Add a new grant"):
        with st.form("add_grant"):
            fc1, fc2 = st.columns(2)
            g_id     = fc1.text_input("Grant ID",    placeholder="ERC-2026-STG-EXAMPLE")
            g_title  = fc2.text_input("Title",       placeholder="My Research Project")
            fc3, fc4 = st.columns(2)
            g_funder = fc3.text_input("Funder",      placeholder="European Research Council")
            g_type   = fc4.text_input("Grant type",  placeholder="ERC Starting Grant")
            fc5, fc6 = st.columns(2)
            g_start  = fc5.date_input("Start date")
            g_end    = fc6.date_input("End date")
            fc7, fc8 = st.columns(2)
            g_budget = fc7.number_input("Total budget (EUR)", min_value=0, step=10000)
            g_pi     = fc8.text_input("Principal Investigator")
            g_desc   = st.text_area("Description (optional)")

            st.markdown("**Team members** — one per line: `Name, Role, YYYY-MM-DD`")
            g_team_raw = st.text_area(
                "team_raw", label_visibility="collapsed",
                placeholder="Alice Smith, Postdoc, 2026-01-01\nBob Jones, PhD Student, 2026-03-01",
            )

            submitted = st.form_submit_button("Add grant", type="primary")
            if submitted:
                if not g_id or not g_title:
                    st.error("Grant ID and Title are required.")
                else:
                    team = []
                    for line in g_team_raw.strip().splitlines():
                        parts = [p.strip() for p in line.split(",")]
                        if len(parts) >= 2:
                            team.append({
                                "name":   parts[0],
                                "role":   parts[1],
                                "joined": parts[2] if len(parts) > 2 else "",
                            })
                    new_grant = {
                        "id":               g_id,
                        "title":            g_title,
                        "funder":           g_funder,
                        "type":             g_type,
                        "start_date":       g_start.strftime("%Y-%m-%d"),
                        "end_date":         g_end.strftime("%Y-%m-%d"),
                        "total_budget_eur": int(g_budget),
                        "status":           "active",
                        "pi":               g_pi,
                        "description":      g_desc,
                        "team":             team,
                        "work_packages":    [],
                    }
                    all_grants = load_grants()
                    all_grants.append(new_grant)
                    save_grants(all_grants)
                    st.success(f"Grant '{g_title}' added.")
                    st.rerun()


# ── Deadlines tab ─────────────────────────────────────────────────────────────

DEADLINES_CACHE = ROOT / "data" / "deadlines.json"

from typing import Optional, Literal
from pydantic import BaseModel as PydanticBaseModel, Field as PydanticField


class DeadlineItem(PydanticBaseModel):
    title: str = PydanticField(description="Short description of the task or deadline")
    date: str  = PydanticField(description="Date in YYYY-MM-DD format")
    source: str = PydanticField(description="Which file or section this came from")
    owner: Optional[str] = PydanticField(None, description="Person responsible, if mentioned")
    item_type: Literal["deadline", "action_item", "milestone", "meeting", "report_due"] = PydanticField(
        description="Category of the item"
    )
    notes: str = PydanticField("", description="Any extra context or detail")


class DeadlineList(PydanticBaseModel):
    items: list[DeadlineItem]


def _extract_deadlines_llm() -> list[dict]:
    """Extract structured deadlines from all available sources via LLM.

    Sources: emails/, lab_notes/, and milestone/end-date data from grants.json.
    Uses instructor structured output (DeadlineList) so the result is always
    a typed list.  Results are cached in deadlines.json by the caller.

    Returns a list of dicts matching the DeadlineItem schema.
    """
    from worker_logic import load_emails, load_lab_notes
    import json as _json

    today_str = datetime.today().strftime("%Y-%m-%d")
    emails    = load_emails()
    lab_notes = load_lab_notes()

    grants_raw = ""
    if GRANTS_FILE.exists():
        gdata = _json.loads(GRANTS_FILE.read_text(encoding="utf-8"))
        for g in gdata.get("grants", []):
            grants_raw += (
                f"Grant: {g['title']} (ID: {g['id']})\n"
                f"  End date: {g['end_date']}\n"
                f"  Work packages: {', '.join(wp['title'] + ' [' + wp['status'] + ']' for wp in g.get('work_packages', []))}\n"
            )

    prompt = (
        f"Today is {today_str}. Extract every concrete deadline, action item, meeting, "
        f"milestone, or report due date from the text below. "
        f"Convert relative dates (e.g. 'next Thursday', 'end of July') to absolute YYYY-MM-DD dates based on today. "
        f"Include grant end dates and work package milestones from the grants section. "
        f"Ignore vague statements with no date. Return only items with a clear date.\n\n"
        f"=== EMAILS ===\n{emails}\n\n"
        f"=== LAB NOTES ===\n{lab_notes}\n\n"
        f"=== GRANTS ===\n{grants_raw}"
    )

    client = get_instructor_client()
    result = client.chat.completions.create(
        model=VENICE_MODEL,
        max_tokens=2000,
        response_model=DeadlineList,
        messages=[
            {"role": "system", "content": "You are a precise information extractor. Extract structured deadline data from the provided text."},
            {"role": "user",   "content": prompt},
        ],
    )
    return [item.model_dump() for item in result.items]


def load_deadlines_cache() -> list[dict]:
    if DEADLINES_CACHE.exists():
        import json as _json
        data = _json.loads(DEADLINES_CACHE.read_text(encoding="utf-8"))
        return data.get("items", [])
    return []


def save_deadlines_cache(items: list[dict]) -> None:
    import json as _json
    DEADLINES_CACHE.write_text(
        _json.dumps({"extracted_at": datetime.utcnow().isoformat() + "Z", "items": items}, indent=2),
        encoding="utf-8",
    )


_TYPE_ICON = {
    "deadline":    "🔴",
    "action_item": "✅",
    "milestone":   "🏁",
    "meeting":     "📅",
    "report_due":  "📋",
}
_TYPE_LABEL = {
    "deadline":    "Deadline",
    "action_item": "Action item",
    "milestone":   "Milestone",
    "meeting":     "Meeting",
    "report_due":  "Report due",
}


def _urgency(date_str: str) -> tuple[int, str, str]:
    """Classify a date string into an urgency tier for display.

    Returns:
        sort_key    — integer for ordering groups (0 = overdue, 1 = this week, …)
        group_label — human-readable group heading with emoji
        colour      — hex colour for badges and calendar cells
    """
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return (99, "Unknown date", "#888888")
    today = datetime.today().date()
    delta = (d - today).days
    if delta < 0:
        return (0, "🔴 Overdue", "#c0392b")
    elif delta <= 7:
        return (1, "🟠 This week", "#e67e22")
    elif delta <= 30:
        return (2, "🟡 This month", "#f1c40f")
    else:
        return (3, "🟢 Later", "#27ae60")


with tab_deadlines:
    items = load_deadlines_cache()

    extracted_at = ""
    if DEADLINES_CACHE.exists():
        import json as _json
        _meta = _json.loads(DEADLINES_CACHE.read_text(encoding="utf-8"))
        extracted_at = _meta.get("extracted_at", "")[:10]

    hcol, bcol = st.columns([4, 1])
    with hcol:
        st.subheader("Deadlines & Action Items")
        if extracted_at:
            st.caption(f"Last extracted: {extracted_at}")
        else:
            st.caption("No data yet — click **Extract** to scan emails, lab notes, and grants.")
    with bcol:
        if st.button("🔄 Extract / Refresh", type="primary", use_container_width=True):
            with st.spinner("Scanning emails, lab notes, and grants for dates…"):
                try:
                    items = _extract_deadlines_llm()
                    save_deadlines_cache(items)
                    st.success(f"Found {len(items)} items.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Extraction failed: {e}")

    if not items:
        st.info("Click **Extract / Refresh** to scan your sources for deadlines and action items.")
    else:
        import calendar as _cal
        today = datetime.today().date()

        # ── Top metrics ──
        overdue   = sum(1 for it in items if _urgency(it["date"])[0] == 0)
        this_week = sum(1 for it in items if _urgency(it["date"])[0] == 1)
        m1, m2, m3 = st.columns(3)
        m1.metric("Overdue",       overdue)
        m2.metric("Due this week", this_week)
        m3.metric("Total tracked", len(items))

        st.divider()

        view = st.radio("View", ["📋 List", "📅 Calendar"], horizontal=True, label_visibility="collapsed")

        # ── Build date → items lookup ──
        date_map: dict = {}
        for it in items:
            date_map.setdefault(it["date"], []).append(it)

        # ════════════════════════════════════════════════════
        if view == "📋 List":
            sorted_items = sorted(items, key=lambda it: (_urgency(it["date"])[0], it["date"]))
            groups: dict[str, list] = {}
            for it in sorted_items:
                _, group_label, _ = _urgency(it["date"])
                groups.setdefault(group_label, []).append(it)

            for group_label, group_items in groups.items():
                _, _, grp_colour = _urgency(group_items[0]["date"])
                st.markdown(
                    f"<h4 style='color:{grp_colour};margin-bottom:4px'>{group_label}</h4>",
                    unsafe_allow_html=True,
                )
                for it in group_items:
                    icon  = _TYPE_ICON.get(it["item_type"], "•")
                    label = _TYPE_LABEL.get(it["item_type"], it["item_type"])
                    try:
                        d = datetime.strptime(it["date"], "%Y-%m-%d").date()
                        delta_days   = (d - today).days
                        date_display = d.strftime("%d %b %Y")
                        date_tag = (
                            f"({abs(delta_days)}d ago)" if delta_days < 0 else
                            "(today)" if delta_days == 0 else
                            f"(in {delta_days}d)"
                        )
                    except ValueError:
                        date_display, date_tag = it["date"], ""
                    _, _, it_colour = _urgency(it["date"])
                    with st.container(border=True):
                        c1, c2 = st.columns([5, 2])
                        with c1:
                            st.markdown(f"{icon} **{it['title']}**")
                            if it.get("notes"):
                                st.caption(it["notes"])
                            tags = f"`{label}`"
                            if it.get("owner"): tags += f"  👤 {it['owner']}"
                            tags += f"  📁 _{it.get('source', '')}_"
                            st.markdown(tags)
                        with c2:
                            st.markdown(
                                f"<div style='text-align:right'>"
                                f"<span style='font-size:1.1em;font-weight:bold'>{date_display}</span><br>"
                                f"<span style='color:{it_colour};font-size:0.85em'>{date_tag}</span>"
                                f"</div>",
                                unsafe_allow_html=True,
                            )

        # ════════════════════════════════════════════════════
        else:
            # Calendar button styles
            st.markdown("""
<style>
div[data-testid="column"] .stButton > button {
    width: 100% !important;
    min-height: 72px !important;
    padding: 6px 2px !important;
    font-size: 1.05em !important;
    font-weight: 600 !important;
    border-radius: 8px !important;
    white-space: pre-line !important;
    line-height: 1.5 !important;
    text-align: center !important;
    transition: background 0.15s, border-color 0.15s !important;
}
div[data-testid="column"] .stButton > button:hover {
    border-color: #4A90D9 !important;
    background: #4A90D915 !important;
}
</style>""", unsafe_allow_html=True)

            # Calendar navigation state
            if "cal_year"  not in st.session_state: st.session_state.cal_year  = today.year
            if "cal_month" not in st.session_state: st.session_state.cal_month = today.month
            if "selected_cal_date" not in st.session_state: st.session_state.selected_cal_date = None

            nav1, nav2, nav3 = st.columns([1, 3, 1])
            with nav1:
                if st.button("◀ Prev", key="cal_prev"):
                    if st.session_state.cal_month == 1:
                        st.session_state.cal_month = 12
                        st.session_state.cal_year -= 1
                    else:
                        st.session_state.cal_month -= 1
                    st.session_state.selected_cal_date = None
                    st.rerun()
            with nav2:
                st.markdown(
                    f"<h3 style='text-align:center;margin:0'>"
                    f"{_cal.month_name[st.session_state.cal_month]} {st.session_state.cal_year}"
                    f"</h3>",
                    unsafe_allow_html=True,
                )
            with nav3:
                if st.button("Next ▶", key="cal_next"):
                    if st.session_state.cal_month == 12:
                        st.session_state.cal_month = 1
                        st.session_state.cal_year += 1
                    else:
                        st.session_state.cal_month += 1
                    st.session_state.selected_cal_date = None
                    st.rerun()

            cy, cm = st.session_state.cal_year, st.session_state.cal_month
            _, num_days = _cal.monthrange(cy, cm)
            first_weekday = _cal.monthrange(cy, cm)[0]

            DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
            header_cols = st.columns(7)
            for i, d in enumerate(DOW):
                header_cols[i].markdown(
                    f"<div style='text-align:center;font-weight:bold;color:#888;"
                    f"border-bottom:1px solid #444;padding-bottom:6px;margin-bottom:4px'>{d}</div>",
                    unsafe_allow_html=True,
                )

            # Build weeks as clickable buttons
            day_num   = 1
            cell_slot = first_weekday
            week_days: list = [None] * 7

            while day_num <= num_days:
                week_days[cell_slot] = day_num
                cell_slot += 1
                if cell_slot == 7 or day_num == num_days:
                    cols = st.columns(7)
                    for col_i, dn in enumerate(week_days):
                        if dn is None:
                            cols[col_i].markdown(" ")
                        else:
                            ds        = f"{cy:04d}-{cm:02d}-{dn:02d}"
                            day_items = date_map.get(ds, [])
                            is_today  = (dn == today.day and cm == today.month and cy == today.year)
                            is_sel    = st.session_state.selected_cal_date == ds

                            # Build label: day number + urgency dots
                            if day_items:
                                _, _, dc = _urgency(ds)
                                dot_str  = " ".join(
                                    ["🔴","🟠","🟡","🟢"][min(_urgency(ds)[0], 3)]
                                    for _ in day_items[:3]
                                )
                                label = f"{dn}\n{dot_str}"
                            else:
                                label = str(dn)

                            # Highlight today / selected via injected CSS trick:
                            # prepend a zero-width marker we can target
                            prefix = "◉ " if is_sel else ("· " if is_today else "")
                            with cols[col_i]:
                                if st.button(
                                    f"{prefix}{label}",
                                    key=f"cal_{ds}",
                                    use_container_width=True,
                                    type="primary" if is_sel else "secondary",
                                ):
                                    if st.session_state.selected_cal_date == ds:
                                        st.session_state.selected_cal_date = None
                                    else:
                                        st.session_state.selected_cal_date = ds
                                    st.rerun()

                    week_days = [None] * 7
                    cell_slot = 0
                day_num += 1

            # ── Detail panel ──────────────────────────────────────────
            sel = st.session_state.selected_cal_date
            if sel:
                sel_items = date_map.get(sel, [])
                try:
                    sel_d    = datetime.strptime(sel, "%Y-%m-%d").date()
                    d_label  = sel_d.strftime("%A, %d %B %Y")
                    delta    = (sel_d - today).days
                    if delta < 0:    when = f"{abs(delta)} days ago"
                    elif delta == 0: when = "today"
                    else:            when = f"in {delta} days"
                except ValueError:
                    d_label, when = sel, ""

                _, _, panel_colour = _urgency(sel)

                st.markdown(
                    f"""
<div style="margin-top:16px;padding:16px 20px;border-radius:10px;
            border-left:5px solid {panel_colour};
            background:{panel_colour}18;
            animation:slideIn .2s ease">
  <div style="font-size:1.2em;font-weight:700">{d_label}</div>
  <div style="color:{panel_colour};font-size:0.9em;margin-bottom:12px">{when}</div>
</div>
<style>
@keyframes slideIn {{
  from {{ opacity:0; transform: translateY(-10px); }}
  to   {{ opacity:1; transform: translateY(0); }}
}}
</style>""",
                    unsafe_allow_html=True,
                )

                if not sel_items:
                    st.caption("No items on this day.")
                else:
                    for it in sel_items:
                        icon  = _TYPE_ICON.get(it["item_type"], "•")
                        label = _TYPE_LABEL.get(it["item_type"], it["item_type"])
                        with st.container(border=True):
                            ca, cb = st.columns([5, 1])
                            with ca:
                                st.markdown(f"{icon} **{it['title']}**")
                                if it.get("notes"):
                                    st.caption(it["notes"])
                                tags = f"`{label}`"
                                if it.get("owner"): tags += f"  👤 {it['owner']}"
                                tags += f"  📁 _{it.get('source','')}_"
                                st.markdown(tags)
                            with cb:
                                st.markdown(
                                    f"<div style='text-align:right;color:{panel_colour};"
                                    f"font-weight:bold'>{_TYPE_ICON.get(it['item_type'],'')}</div>",
                                    unsafe_allow_html=True,
                                )
            else:
                # No selection: show compact month summary
                month_items = [it for it in items if it["date"].startswith(f"{cy:04d}-{cm:02d}-")]
                if month_items:
                    st.markdown("---")
                    st.caption(f"{len(month_items)} item(s) this month — click a day to expand")
                else:
                    st.caption(f"No items in {_cal.month_name[cm]} {cy}.")


# ── Chat tab ──────────────────────────────────────────────────────────────────
with tab_chat:
    _, _chat_center, _ = st.columns([1, 8, 1])

    def _render_message(msg: dict):
        import markdown as _md
        role = msg["role"]
        with _chat_center:
            if role == "user":
                st.markdown(
                    f'<div class="chat-wrap user">'
                    f'  <div class="chat-avatar user">👤</div>'
                    f'  <div class="chat-bubble user">{msg["content"]}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
            else:
                badges_html = ""
                if msg.get("workers"):
                    badges = " · ".join(WORKER_BADGE.get(w, w) for w in msg["workers"])
                    badges_html = f'<div class="chat-badge">🤖 {badges}</div>'
                content_html = _md.markdown(
                    msg["content"],
                    extensions=["extra", "nl2br"],
                )
                st.markdown(
                    f'<div class="chat-wrap ai">'
                    f'  <div class="chat-avatar ai">🔬</div>'
                    f'  <div style="flex:1">'
                    f'    {badges_html}'
                    f'    <div class="chat-bubble ai">{content_html}</div>'
                    f'  </div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
                # Pending actions: expense editor or email draft
                action = msg.get("pending_action")
                msg_idx = msg.get("_idx")
                if action and msg_idx is not None:

                    # ── Email draft ──────────────────────────────────────────
                    if action.get("type") == "email_draft":
                        import urllib.parse as _up
                        done_key = f"email_done_{msg_idx}"
                        if not st.session_state.get(done_key):
                            content_key = f"email_content_{msg_idx}"
                            recip_key   = f"email_recip_{msg_idx}"
                            subj_key    = f"email_subj_{msg_idx}"
                            if content_key not in st.session_state:
                                # Strip the "Subject: ..." line from the body
                                raw_lines = action["content"].splitlines()
                                body_only = "\n".join(
                                    l for l in raw_lines
                                    if not l.lower().startswith("subject:")
                                ).strip()
                                st.session_state[content_key] = body_only
                            if recip_key not in st.session_state:
                                st.session_state[recip_key] = action.get("recipient", "")
                            if subj_key not in st.session_state:
                                st.session_state[subj_key] = action.get("subject", "")

                            r1, r2 = st.columns([1, 2])
                            r1.text_input("To:", key=recip_key, placeholder="recipient@example.com")
                            r2.text_input("Subject:", key=subj_key, placeholder="Email subject")
                            edited = st.text_area(
                                "Email body",
                                value=st.session_state[content_key],
                                height=260,
                                key=f"email_area_{msg_idx}",
                            )
                            st.session_state[content_key] = edited

                            # Build mailto from current widget values
                            recip     = st.session_state.get(recip_key, "")
                            subj      = st.session_state.get(subj_key, "")
                            body_text = edited.strip()
                            mailto = (
                                f"mailto:{recip}"
                                f"?subject={_up.quote(subj)}"
                                f"&body={_up.quote(body_text)}"
                            )

                            ea, eb, _ = st.columns([2, 1, 3])
                            ea.markdown(
                                f'<a href="{mailto}" target="_blank">'
                                f'<button style="width:100%;padding:8px 12px;border-radius:6px;'
                                f'border:1px solid #2d3348;background:#4A90D9;color:white;'
                                f'cursor:pointer;font-size:0.9rem;font-weight:600">'
                                f'📤 Open in mail app</button></a>',
                                unsafe_allow_html=True,
                            )
                            if eb.button("✖ Dismiss", key=f"email_dismiss_{msg_idx}"):
                                st.session_state[done_key] = True
                                st.rerun()
                        else:
                            st.caption("✉️ Email draft dismissed.")

                    # ── Expense row editor ───────────────────────────────────
                    else:
                        confirmed = st.session_state.get(f"action_done_{msg_idx}")
                        if not confirmed:
                            edit_key = f"edit_row_{msg_idx}"
                            if edit_key not in st.session_state:
                                st.session_state[edit_key] = dict(action)

                            row = st.session_state[edit_key]
                            st.markdown(
                                "<div style='background:#1e2130;border:1px solid #2d3348;"
                                "border-radius:10px;padding:14px 16px;margin:8px 0'>",
                                unsafe_allow_html=True,
                            )
                            e1, e2 = st.columns(2)
                            row["Date"]        = e1.text_input("Date",        value=row["Date"],        key=f"e_date_{msg_idx}")
                            row["Category"]    = e2.selectbox("Category",
                                ["Personnel","Compute","Travel","Equipment","Subcontracting","Other"],
                                index=["Personnel","Compute","Travel","Equipment","Subcontracting","Other"].index(row["Category"])
                                    if row["Category"] in ["Personnel","Compute","Travel","Equipment","Subcontracting","Other"] else 0,
                                key=f"e_cat_{msg_idx}")
                            row["Description"] = st.text_input("Description", value=row["Description"], key=f"e_desc_{msg_idx}")
                            e3, e4 = st.columns(2)
                            row["Amount_EUR"]        = e3.number_input("Amount (EUR)", value=float(row["Amount_EUR"]), min_value=0.0, step=10.0, key=f"e_amt_{msg_idx}")
                            row["ERC_Budget_Line"]   = e4.text_input("ERC budget line", value=row["ERC_Budget_Line"], key=f"e_bline_{msg_idx}")
                            row["Compliance_Status"] = st.selectbox("Status",
                                ["Approved", "Pending Audit", "Rejected"],
                                index=["Approved","Pending Audit","Rejected"].index(row["Compliance_Status"])
                                    if row["Compliance_Status"] in ["Approved","Pending Audit","Rejected"] else 1,
                                key=f"e_status_{msg_idx}")
                            st.markdown("</div>", unsafe_allow_html=True)

                            ca, cb, _ = st.columns([1, 1, 4])
                            if ca.button("✅ Confirm", key=f"confirm_{msg_idx}", type="primary"):
                                try:
                                    _apply_expense_row(st.session_state[edit_key])
                                    st.session_state[f"action_done_{msg_idx}"] = "confirmed"
                                    st.session_state[f"saved_id_{msg_idx}"] = st.session_state[edit_key]["Transaction_ID"]
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"Failed to save: {e}")
                            if cb.button("❌ Cancel", key=f"cancel_{msg_idx}"):
                                st.session_state[f"action_done_{msg_idx}"] = "cancelled"
                                st.rerun()
                        elif confirmed == "confirmed":
                            st.success(f"✅ Expense `{st.session_state.get(f'saved_id_{msg_idx}', '')}` saved to spreadsheet.")
                        elif confirmed == "cancelled":
                            st.caption("Cancelled — no changes made.")

    # Render history
    for i, msg in enumerate(st.session_state.messages):
        msg["_idx"] = i
        _render_message(msg)

    # Handle example button click — input stays full-width at bottom
    if st.session_state.pending_input:
        user_text = st.session_state.pending_input
        st.session_state.pending_input = None
    else:
        user_text = st.chat_input("Ask anything about your grant, expenses, research progress...")

    if user_text:
        st.session_state.messages.append({"role": "user", "content": user_text})
        _render_message({"role": "user", "content": user_text})

        with _chat_center:
            with st.spinner("Thinking..."):
                try:
                    answer, used_workers, pending_action = dispatch(user_text)
                except Exception as e:
                    answer        = f"⚠️ Something went wrong: {e}"
                    used_workers  = []
                    pending_action = None

        new_msg = {
            "role": "assistant",
            "content": answer,
            "workers": used_workers,
            "pending_action": pending_action,
            "_idx": len(st.session_state.messages),
        }
        st.session_state.messages.append(new_msg)
        _render_message(new_msg)
        st.rerun()
