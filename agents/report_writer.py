from uagents import Agent, Context
from pathlib import Path
from datetime import datetime
import requests
from openai import OpenAI
from models import TaskRequest, WorkerResponse
from github_config import GITHUB_REPO, GITHUB_TOKEN

llm = OpenAI(
    base_url="https://api.venice.ai/api/v1",
    api_key="VENICE_INFERENCE_KEY_-ZMGJZ9LK-Gnw-yh-BCecTz6UVBRzGkkrLx6npnF7K",
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
KB_DIR      = PROJECT_ROOT / "knowledge_base"
LAB_DIR     = PROJECT_ROOT / "lab_notes"

report_writer = Agent(
    name="ReportWriter",
    port=8004,
    seed="report_writer_secret_seed_phrase_2026",
    endpoint=["http://127.0.0.1:8004/submit"],
    mailbox="YOUR_REPORT_WRITER_MAILBOX_KEY",
    network="testnet",
)


def load_template() -> str:
    for pattern in ["*template*", "*report_scheme*", "*report_template*"]:
        for ext in [".md", ".txt"]:
            matches = list(KB_DIR.glob(f"{pattern}{ext}"))
            if matches:
                return matches[0].read_text(encoding="utf-8")
    return "(No report template found in knowledge_base/)"


def load_lab_notes() -> str:
    LAB_DIR.mkdir(exist_ok=True)
    entries = []
    for f in sorted(LAB_DIR.iterdir()):
        if f.suffix in {".md", ".txt", ".py", ".ipynb"}:
            entries.append(f"=== {f.name} ===\n{f.read_text(encoding='utf-8', errors='replace')}")
    return "\n\n".join(entries) if entries else "(No lab notes found in lab_notes/)"


def fetch_github_activity() -> str:
    if GITHUB_REPO == "YOUR_ORG/YOUR_REPO":
        return "(GitHub not configured — edit agents/github_config.py to set GITHUB_REPO)"

    headers = {}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"token {GITHUB_TOKEN}"

    parts = []

    # Commits
    try:
        r = requests.get(
            f"https://api.github.com/repos/{GITHUB_REPO}/commits",
            headers=headers, params={"per_page": 20}, timeout=10,
        )
        r.raise_for_status()
        commits = r.json()
        if isinstance(commits, list):
            lines = [
                f"  [{c['sha'][:7]}] {c['commit']['message'].splitlines()[0]} "
                f"({c['commit']['author']['date'][:10]})"
                for c in commits
            ]
            parts.append("Recent commits:\n" + "\n".join(lines))
    except Exception as e:
        parts.append(f"Commits fetch failed: {e}")

    # Open issues / PRs
    try:
        r = requests.get(
            f"https://api.github.com/repos/{GITHUB_REPO}/issues",
            headers=headers, params={"state": "open", "per_page": 10}, timeout=10,
        )
        r.raise_for_status()
        issues = r.json()
        if isinstance(issues, list) and issues:
            lines = [f"  #{i['number']} {i['title']}" for i in issues]
            parts.append("Open issues/PRs:\n" + "\n".join(lines))
    except Exception:
        pass

    return "\n\n".join(parts) if parts else "No GitHub activity retrieved."


@report_writer.on_message(model=TaskRequest)
async def handle_report_request(ctx: Context, sender: str, msg: TaskRequest):
    ctx.logger.info("Generating grant progress report...")

    template        = load_template()
    lab_notes       = load_lab_notes()
    github_activity = fetch_github_activity()

    system_prompt = (
        "You are an experienced scientific grant report writer. "
        "Your task is to produce a structured progress report that follows the provided template exactly. "
        "Base the content on the researcher's lab notes and GitHub activity supplied below. "
        "Be specific — cite actual experiments, results, and commit messages. "
        "Maintain a professional academic tone appropriate for an ERC report. "
        "Fill every section of the template; write 'N/A for this period' only when genuinely nothing applies.\n\n"
        f"REPORT TEMPLATE:\n{template}\n\n"
        f"LAB NOTES:\n{lab_notes}\n\n"
        f"GITHUB ACTIVITY:\n{github_activity}"
    )

    result = llm.chat.completions.create(
        model="llama-3.3-70b",
        max_tokens=3000,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": msg.query},
        ],
    )
    report_text = result.choices[0].message.content

    reports_dir = PROJECT_ROOT / "reports"
    reports_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = reports_dir / f"grant_report_{timestamp}.md"
    out_path.write_text(report_text, encoding="utf-8")
    ctx.logger.info(f"Report saved to {out_path}")

    await ctx.send(sender, WorkerResponse(
        request_id=msg.id,
        data=f"Report saved to `reports/grant_report_{timestamp}.md`",
    ))


if __name__ == "__main__":
    report_writer.run()
