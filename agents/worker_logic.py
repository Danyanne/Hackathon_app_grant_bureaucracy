"""
Pure helper functions shared by report_writer.py (agent) and streamlit_app.py (UI).
No uagents imports — safe to import from any thread.
"""
import sys
from pathlib import Path
from datetime import datetime
import requests
from openai import OpenAI

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
from config import VENICE_API_KEY, VENICE_BASE_URL, VENICE_MODEL, GITHUB_REPO, GITHUB_TOKEN, KNOWLEDGE_BASE, LAB_NOTES, EMAILS_DIR, PAPERS_DIR

llm  = OpenAI(base_url=VENICE_BASE_URL, api_key=VENICE_API_KEY)

KB_DIR = KNOWLEDGE_BASE

REPORT_KEYWORDS = {"report", "write", "draft", "generate", "summarise", "summarize"}


# ── Template ──────────────────────────────────────────────────────────────────

def load_template() -> str:
    for pattern in ["*template*", "*report_scheme*", "*report_template*"]:
        for ext in [".md", ".txt"]:
            matches = list(KB_DIR.glob(f"{pattern}{ext}"))
            if matches:
                return matches[0].read_text(encoding="utf-8")
    return "(No report template found in knowledge_base/)"


# ── Lab notes ─────────────────────────────────────────────────────────────────

def _gh_headers() -> dict:
    return {"Authorization": f"token {GITHUB_TOKEN}"} if GITHUB_TOKEN else {}


def fetch_lab_notes_from_github() -> str:
    if GITHUB_REPO == "YOUR_ORG/YOUR_REPO":
        return ""
    try:
        r = requests.get(
            f"https://api.github.com/repos/{GITHUB_REPO}/contents/docs/lab_notebook",
            headers=_gh_headers(), timeout=10,
        )
        if r.status_code != 200:
            return ""
        entries = []
        for item in sorted(r.json(), key=lambda x: x["name"]):
            if item["name"].endswith(".md"):
                cr = requests.get(item["download_url"], headers=_gh_headers(), timeout=10)
                if cr.status_code == 200:
                    entries.append(f"=== {item['name']} ===\n{cr.text}")
        return "\n\n".join(entries)
    except Exception:
        return ""


def load_lab_notes() -> str:
    github_notes = fetch_lab_notes_from_github()
    if github_notes:
        return github_notes
    LAB_NOTES.mkdir(exist_ok=True)
    entries = []
    for f in sorted(LAB_NOTES.iterdir()):
        if f.suffix in {".md", ".txt", ".py", ".ipynb"}:
            entries.append(f"=== {f.name} ===\n{f.read_text(encoding='utf-8', errors='replace')}")
    return "\n\n".join(entries) if entries else "(No lab notes found)"


# ── Emails ────────────────────────────────────────────────────────────────────

def load_emails() -> str:
    EMAILS_DIR.mkdir(exist_ok=True)
    entries = []
    for f in sorted(EMAILS_DIR.iterdir()):
        if f.suffix == ".txt":
            entries.append(f"=== {f.name} ===\n{f.read_text(encoding='utf-8', errors='replace')}")
    return "\n\n".join(entries) if entries else "(No email threads found in emails/)"


# ── Papers (Overleaf exports) ─────────────────────────────────────────────────

def load_papers() -> str:
    PAPERS_DIR.mkdir(exist_ok=True)
    entries = []
    for f in sorted(PAPERS_DIR.iterdir()):
        if f.suffix in {".tex", ".bib"}:
            entries.append(f"=== {f.name} ===\n{f.read_text(encoding='utf-8', errors='replace')}")
    return "\n\n".join(entries) if entries else ""


# ── GitHub commits ────────────────────────────────────────────────────────────

def fetch_github_activity() -> str:
    if GITHUB_REPO == "YOUR_ORG/YOUR_REPO":
        return "(GitHub not configured — edit agents/config.py to set GITHUB_REPO)"
    parts = []
    try:
        r = requests.get(
            f"https://api.github.com/repos/{GITHUB_REPO}/commits",
            headers=_gh_headers(), params={"per_page": 30}, timeout=10,
        )
        if r.status_code == 404:
            return f"(Repo '{GITHUB_REPO}' not found — check GITHUB_REPO in config.py)"
        if r.status_code in (401, 403):
            return "(GitHub auth failed — check GITHUB_TOKEN in config.py)"
        r.raise_for_status()
        commits = r.json()
        if isinstance(commits, list):
            lines = [
                f"  [{c['sha'][:7]}] {c['commit']['message'].splitlines()[0]} "
                f"({(c['commit'].get('author') or {}).get('date', '')[:10]})"
                for c in commits
            ]
            parts.append("Commits (newest first):\n" + "\n".join(lines))
    except Exception as e:
        parts.append(f"Commits fetch failed: {e}")
    try:
        r = requests.get(
            f"https://api.github.com/repos/{GITHUB_REPO}/issues",
            headers=_gh_headers(), params={"state": "open", "per_page": 10}, timeout=10,
        )
        r.raise_for_status()
        issues = r.json()
        if isinstance(issues, list) and issues:
            lines = [
                f"  #{i['number']} [{'PR' if 'pull_request' in i else 'issue'}] {i['title']}"
                for i in issues
            ]
            parts.append("Open issues/PRs:\n" + "\n".join(lines))
    except Exception:
        pass
    return "\n\n".join(parts) if parts else "No GitHub activity retrieved."


# ── Report generation ─────────────────────────────────────────────────────────

_TRUNC = {
    "template":        1_500,
    "lab_notes":       1_500,
    "emails":            800,
    "github_activity": 1_200,
    "papers":            600,
}


def _t(text: str, key: str) -> str:
    limit = _TRUNC[key]
    return text[:limit] + "\n…[truncated]" if len(text) > limit else text


def generate_report_response(query: str) -> tuple[str, str | None]:
    """
    Returns (chat_response, saved_path_or_None).
    Full report  → saves .md, returns (text, path).
    Quick query  → returns (LLM answer, None).
    """
    lab_notes       = _t(load_lab_notes(),       "lab_notes")
    emails          = _t(load_emails(),           "emails")
    github_activity = _t(fetch_github_activity(), "github_activity")
    papers          = load_papers()
    is_full_report  = any(k in query.lower() for k in REPORT_KEYWORDS)

    papers_block = f"\n\nPAPERS / DRAFTS:\n{_t(papers, 'papers')}" if papers else ""

    if is_full_report:
        system_prompt = (
            "You are an experienced scientific grant report writer. "
            "Write a structured ERC progress report with these sections: "
            "1. Scientific Progress, 2. Personnel, 3. Budget Status, "
            "4. Upcoming Milestones, 5. Risks & Deviations. "
            "Be specific — cite actual experiments, commits, and results from the data below. "
            "Professional academic tone. Keep each section concise but substantive.\n\n"
            f"LAB NOTES:\n{lab_notes}\n\n"
            f"EMAIL THREADS:\n{emails}\n\n"
            f"GITHUB ACTIVITY:\n{github_activity}"
            f"{papers_block}"
        )
        _r = llm.chat.completions.create(
            model=VENICE_MODEL,
            max_tokens=4000,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": query},
            ],
        )
        report_text = (_r.choices[0].message.content or "").strip() if _r.choices else ""
        if not report_text:
            finish = (_r.choices[0].finish_reason or "unknown") if _r.choices else "no_choices"
            raise RuntimeError(
                f"LLM returned empty content (finish_reason={finish}). "
                "The context may be too large — try a shorter query or retry."
            )
        reports_dir = PROJECT_ROOT / "reports"
        reports_dir.mkdir(exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path  = reports_dir / f"grant_report_{timestamp}.md"
        out_path.write_text(report_text, encoding="utf-8")
        return report_text, str(out_path)

    else:
        _r = llm.chat.completions.create(
            model=VENICE_MODEL,
            max_tokens=512,
            messages=[
                {"role": "system", "content": (
                    "You are a helpful research assistant. "
                    "Answer the user's question concisely and directly, "
                    "drawing on the lab notebook, email threads, GitHub commits, and paper drafts below.\n\n"
                    f"LAB NOTES:\n{lab_notes}\n\n"
                    f"EMAIL THREADS:\n{emails}\n\n"
                    f"GITHUB ACTIVITY:\n{github_activity}"
                    f"{papers_block}"
                )},
                {"role": "user", "content": query},
            ],
        )
        text = (_r.choices[0].message.content or "").strip() if _r.choices else ""
        return text or "⚠️ No response generated.", None
