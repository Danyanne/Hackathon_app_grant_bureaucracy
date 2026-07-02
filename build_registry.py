"""
build_registry.py — Rebuild worker_registry.json.

For each data source (policy PDFs, expense Excel, GitHub commits, lab notes,
emails, paper drafts) the LLM produces a 2-4 sentence plain-English summary.
The resulting JSON is read by streamlit_app.analyze_intent() and
agents/orchestrator.py to build routing prompts that describe what each worker
actually knows, so the router can make better decisions as data changes.

Run after any of the following:
    - Adding or updating files in knowledge_base/, lab_notes/, emails/, papers/
    - Changing GITHUB_REPO or GITHUB_TOKEN in agents/github_config.py

    python3 build_registry.py
"""
import json
import sys
from pathlib import Path
from datetime import datetime

import pandas as pd
import requests
import pypdf
from openai import OpenAI

from config import (
    VENICE_API_KEY, VENICE_BASE_URL, VENICE_MODEL,
    GITHUB_REPO, GITHUB_TOKEN,
    PROJECT_ROOT, KNOWLEDGE_BASE as KB_DIR,
    LAB_NOTES as LAB_DIR, EMAILS_DIR, PAPERS_DIR, REGISTRY_FILE as REGISTRY,
)

llm = OpenAI(base_url=VENICE_BASE_URL, api_key=VENICE_API_KEY)


def _summarise(content: str, instruction: str) -> str:
    """Ask the LLM to summarise content according to a specific instruction."""
    result = llm.chat.completions.create(
        model=VENICE_MODEL,
        max_tokens=300,
        messages=[
            {"role": "system", "content": instruction},
            {"role": "user",   "content": content[:8000]},   # hard cap to avoid huge tokens
        ],
    )
    return result.choices[0].message.content.strip()


# ── COMPLIANCE: PDF documents ─────────────────────────────────────────────────

def build_compliance_sources() -> list:
    sources = []
    for pdf_path in sorted(KB_DIR.glob("*.pdf")):
        print(f"  Reading {pdf_path.name}...")
        reader = pypdf.PdfReader(str(pdf_path))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        summary = _summarise(
            text,
            "You are indexing a document for a question-routing system. "
            "In 3-4 sentences, describe: what this document covers, what rules or limits it contains, "
            "and what kinds of questions a researcher could answer by reading it. "
            "Be concrete — mention specific numbers, categories, or requirements if present. "
            "Do NOT use bullet points, just plain sentences."
        )
        sources.append({"file": pdf_path.name, "summary": summary})
    return sources


# ── DATA WORKER: spreadsheets ─────────────────────────────────────────────────

def build_data_worker_sources() -> list:
    sources = []
    for xls_path in sorted(KB_DIR.glob("*.xlsx")):
        print(f"  Reading {xls_path.name}...")
        df = pd.read_excel(xls_path)
        # Build a compact text description of the actual data
        cats = df["Category"].unique().tolist() if "Category" in df.columns else []
        statuses = df["Compliance_Status"].unique().tolist() if "Compliance_Status" in df.columns else []
        date_col = next((c for c in df.columns if "date" in c.lower()), None)
        try:
            date_range = (
                f"{pd.to_datetime(df[date_col]).min().date()} to "
                f"{pd.to_datetime(df[date_col]).max().date()}"
            ) if date_col else "unknown"
        except Exception:
            date_range = f"{df[date_col].iloc[0]} to {df[date_col].iloc[-1]}"
        data_description = (
            f"File: {xls_path.name}\n"
            f"Rows: {len(df)}\n"
            f"Columns: {', '.join(df.columns)}\n"
            f"Date range: {date_range}\n"
            f"Categories: {', '.join(str(c) for c in cats)}\n"
            f"Compliance statuses: {', '.join(str(s) for s in statuses)}\n"
            f"Sample rows:\n{df.head(5).to_string(index=False)}"
        )
        summary = _summarise(
            data_description,
            "You are indexing a dataset for a question-routing system. "
            "In 2-3 sentences, describe what expense data this spreadsheet contains, "
            "what time period it covers, what categories of spending appear, "
            "and what kinds of financial questions a researcher could answer from it. "
            "Be concrete — mention the actual categories and date range."
        )
        sources.append({"file": xls_path.name, "summary": summary})
    return sources


# ── REPORT WRITER: GitHub commits + lab notes ─────────────────────────────────

def _gh_headers() -> dict:
    return {"Authorization": f"token {GITHUB_TOKEN}"} if GITHUB_TOKEN else {}


def build_report_writer_sources() -> list:
    sources = []

    if GITHUB_REPO and GITHUB_REPO != "YOUR_ORG/YOUR_REPO":
        print(f"  Fetching commits from {GITHUB_REPO}...")
        try:
            r = requests.get(
                f"https://api.github.com/repos/{GITHUB_REPO}/commits",
                headers=_gh_headers(), params={"per_page": 30}, timeout=10,
            )
            r.raise_for_status()
            commits = r.json()
            commit_text = "\n".join(
                f"[{c['sha'][:7]}] {c['commit']['message'].splitlines()[0]} "
                f"({(c['commit'].get('author') or {}).get('date','')[:10]})"
                for c in commits if isinstance(commits, list)
            )
            commit_summary = _summarise(
                commit_text,
                "You are indexing a GitHub repository for a question-routing system. "
                "In 2-3 sentences, describe: what research project this repository is about, "
                "what scientific work has been done (mention specific methods, tools, or topics), "
                "and what time period the commit history covers. Be concrete and specific."
            )
            sources.append({
                "type": "github_commits",
                "repo": GITHUB_REPO,
                "summary": commit_summary,
            })
        except Exception as e:
            print(f"  Warning: could not fetch commits — {e}")

        print(f"  Fetching lab notebook from {GITHUB_REPO}...")
        try:
            r = requests.get(
                f"https://api.github.com/repos/{GITHUB_REPO}/contents/docs/lab_notebook",
                headers=_gh_headers(), timeout=10,
            )
            if r.status_code == 200:
                all_notes = []
                for item in sorted(r.json(), key=lambda x: x["name"]):
                    if item["name"].endswith(".md"):
                        cr = requests.get(item["download_url"], headers=_gh_headers(), timeout=10)
                        if cr.status_code == 200:
                            all_notes.append(f"=== {item['name']} ===\n{cr.text}")
                combined = "\n\n".join(all_notes)
                lab_summary = _summarise(
                    combined,
                    "You are indexing a researcher's lab notebook for a question-routing system. "
                    "In 3-4 sentences, describe: what scientific topics and experiments appear in these notes, "
                    "what specific results or findings are mentioned, what time period they cover, "
                    "and who the collaborators are. Be concrete — mention actual experiment names, "
                    "measurements, or scientific terms that appear."
                )
                sources.append({
                    "type": "lab_notes",
                    "repo": GITHUB_REPO,
                    "path": "docs/lab_notebook/",
                    "summary": lab_summary,
                })
        except Exception as e:
            print(f"  Warning: could not fetch lab notes — {e}")

    # Fallback: local lab_notes/ directory
    local_notes = [f for f in LAB_DIR.iterdir() if f.suffix in {".md", ".txt"}] if LAB_DIR.exists() else []
    if local_notes:
        print(f"  Reading {len(local_notes)} local lab note file(s)...")
        combined = "\n\n".join(
            f"=== {f.name} ===\n{f.read_text(encoding='utf-8', errors='replace')}"
            for f in sorted(local_notes)
        )
        lab_summary = _summarise(
            combined,
            "You are indexing a researcher's lab notebook for a question-routing system. "
            "In 3-4 sentences, describe: what scientific topics and experiments appear in these notes, "
            "what specific results or findings are mentioned, what time period they cover, "
            "and who the collaborators are. Be concrete — mention actual experiment names, "
            "measurements, or scientific terms that appear."
        )
        sources.append({
            "type": "lab_notes_local",
            "path": "lab_notes/",
            "summary": lab_summary,
        })

    return sources


# ── EMAILS ────────────────────────────────────────────────────────────────────

def build_email_sources() -> list:
    sources = []
    if not EMAILS_DIR.exists():
        return sources
    txt_files = sorted(EMAILS_DIR.glob("*.txt"))
    if not txt_files:
        return sources
    print(f"  Reading {len(txt_files)} email thread file(s)...")
    combined = "\n\n".join(
        f"=== {f.name} ===\n{f.read_text(encoding='utf-8', errors='replace')}"
        for f in txt_files
    )
    summary = _summarise(
        combined,
        "You are indexing email threads from a research group for a question-routing system. "
        "In 3-4 sentences, describe: what topics are discussed, what decisions or action items "
        "appear, what deadlines are mentioned, and who the participants are. "
        "Be concrete — mention specific scientific topics, dates, names, and any pending tasks."
    )
    sources.append({
        "type": "email_threads",
        "path": "emails/",
        "files": [f.name for f in txt_files],
        "summary": summary,
    })
    return sources


# ── PAPERS (Overleaf .tex exports) ───────────────────────────────────────────

def build_paper_sources() -> list:
    sources = []
    if not PAPERS_DIR.exists():
        return sources
    tex_files = sorted(PAPERS_DIR.glob("*.tex"))
    if not tex_files:
        return sources
    print(f"  Reading {len(tex_files)} .tex file(s) from papers/...")
    combined = "\n\n".join(
        f"=== {f.name} ===\n{f.read_text(encoding='utf-8', errors='replace')}"
        for f in tex_files
    )
    summary = _summarise(
        combined,
        "You are indexing LaTeX source files (paper drafts exported from Overleaf) "
        "for a question-routing system. In 3-4 sentences, describe: what scientific topics "
        "these papers cover, what methods or results are described, what stage of writing "
        "they appear to be at (draft, submitted, etc.), and who the authors are if mentioned. "
        "Be concrete — mention actual scientific terms, methods, and findings."
    )
    sources.append({
        "type": "paper_drafts",
        "path": "papers/",
        "files": [f.name for f in tex_files],
        "summary": summary,
    })
    return sources


# ── MAIN ──────────────────────────────────────────────────────────────────────

def build_registry():
    registry = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "workers": {}
    }

    print("\n[compliance] Summarising policy documents...")
    compliance_sources = build_compliance_sources()
    registry["workers"]["compliance"] = {
        "role": "Answers questions about grant policies, rules, eligibility, and compliance documents.",
        "sources": compliance_sources,
    }

    print("\n[data_worker] Summarising expense data...")
    data_sources = build_data_worker_sources()
    registry["workers"]["data_worker"] = {
        "role": "Answers questions about expense transactions and financial data.",
        "sources": data_sources,
    }

    print("\n[report_writer] Summarising GitHub, lab notes, emails, and papers...")
    report_sources = build_report_writer_sources() + build_email_sources() + build_paper_sources()
    registry["workers"]["report_writer"] = {
        "role": "Answers questions about research progress, generates ERC grant reports.",
        "sources": report_sources,
    }

    REGISTRY.write_text(json.dumps(registry, indent=2), encoding="utf-8")
    print(f"\nRegistry written to {REGISTRY}")
    return registry


if __name__ == "__main__":
    build_registry()
