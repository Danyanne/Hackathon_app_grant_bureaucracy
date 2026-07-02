"""Standalone report generator — runs without any running agents."""
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent / "agents"))

from report_writer import load_template, load_lab_notes, fetch_github_activity
from openai import OpenAI

llm = OpenAI(
    base_url="https://api.venice.ai/api/v1",
    api_key="VENICE_INFERENCE_KEY_-ZMGJZ9LK-Gnw-yh-BCecTz6UVBRzGkkrLx6npnF7K",
)

query = " ".join(sys.argv[1:]) or "Write a full ERC progress report for the last quarter (Q2 2026, April–June 2026)."

print("Fetching data from GitHub...", flush=True)
template        = load_template()
lab_notes       = load_lab_notes()
github_activity = fetch_github_activity()
print(f"  template: {len(template)} chars")
print(f"  lab notes: {len(lab_notes)} chars")
print(f"  github: {len(github_activity)} chars")
print("Generating report with LLM...", flush=True)

result = llm.chat.completions.create(
    model="llama-3.3-70b",
    max_tokens=3000,
    messages=[
        {"role": "system", "content": (
            "You are an experienced scientific grant report writer. "
            "Produce a structured progress report that follows the provided template exactly. "
            "Base the content on the researcher's lab notes and GitHub commits. "
            "Be specific — cite actual experiments, results, and commit messages. "
            "Focus only on work done in the period the user specifies. "
            "Maintain a professional academic tone appropriate for an ERC report.\n\n"
            f"REPORT TEMPLATE:\n{template}\n\n"
            f"LAB NOTES:\n{lab_notes}\n\n"
            f"GITHUB ACTIVITY:\n{github_activity}"
        )},
        {"role": "user", "content": query},
    ],
)
report_text = result.choices[0].message.content

reports_dir = Path(__file__).parent / "reports"
reports_dir.mkdir(exist_ok=True)
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
out_path = reports_dir / f"grant_report_{timestamp}.md"
out_path.write_text(report_text, encoding="utf-8")

print(f"\nReport saved to: {out_path}")
print("-" * 60)
print(report_text[:500] + "..." if len(report_text) > 500 else report_text)
