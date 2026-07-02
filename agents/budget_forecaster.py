"""
budget_forecaster.py — Budget forecasting worker agent.

Projects remaining grant budget consumption using:
- Historical expense data (monthly burn rate per category)
- Lab notes (signals about planned purchases, upcoming needs)
- Grant timeline (days remaining, milestones)
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json
from datetime import datetime
from openai import OpenAI
from uagents import Agent, Context
import pandas as _pd

from models import TaskRequest, WorkerResponse
from config import (
    VENICE_API_KEY, VENICE_BASE_URL, VENICE_MODEL,
    AGENT_SEEDS, AGENT_PORTS,
    KNOWLEDGE_BASE, GRANTS_FILE,
)

llm = OpenAI(base_url=VENICE_BASE_URL, api_key=VENICE_API_KEY)

budget_forecaster = Agent(
    name="BudgetForecaster",
    port=AGENT_PORTS["budget_forecaster"],
    seed=AGENT_SEEDS["budget_forecaster"],
    endpoint=[f"http://127.0.0.1:{AGENT_PORTS['budget_forecaster']}/submit"],
    mailbox=True,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _expense_summary() -> str:
    xls = KNOWLEDGE_BASE / "erc_solar_physics_expenses.xlsx"
    if not xls.exists():
        return "(No expense data found)"
    try:
        import pandas as _pd
        df    = _pd.read_excel(xls)
        total = df["Amount_EUR"].sum() if "Amount_EUR" in df.columns else 0

        by_cat = ""
        if "Category" in df.columns:
            cat_totals = df.groupby("Category")["Amount_EUR"].sum().sort_values(ascending=False)
            by_cat = "\nBy category:\n" + "\n".join(f"  {c}: EUR {v:,.0f}" for c, v in cat_totals.items())

        by_month = ""
        if "Date" in df.columns:
            df["_m"] = _pd.to_datetime(df["Date"], errors="coerce").dt.to_period("M")
            monthly  = df.groupby("_m")["Amount_EUR"].sum().sort_index()
            by_month = "\nMonthly:\n" + "\n".join(f"  {m}: EUR {v:,.0f}" for m, v in monthly.items())

        return f"Total spent: EUR {total:,.0f}{by_cat}{by_month}"
    except Exception as e:
        return f"(Could not read expenses: {e})"


def _grant_context() -> str:
    if not GRANTS_FILE.exists():
        return "(No grants data)"
    try:
        grants = json.loads(GRANTS_FILE.read_text(encoding="utf-8")).get("grants", [])
        lines  = []
        for g in grants:
            budget    = g.get("total_budget_eur", 0)
            end       = g.get("end_date", "")
            days_left = (datetime.strptime(end, "%Y-%m-%d") - datetime.today()).days if end else "?"
            lines.append(f"Grant: {g['title']} | Budget: EUR {budget:,} | End: {end} | Days left: {days_left}")
            for m in g.get("milestones", []):
                lines.append(f"  {m.get('id')}: {m.get('title')} [{m.get('status')}] due {m.get('due_date')}")
        return "\n".join(lines)
    except Exception as e:
        return f"(Grant context error: {e})"


def _compute_forecast_fallback() -> str:
    """Pure-Python budget summary — used when the LLM call fails or returns empty."""
    xls = KNOWLEDGE_BASE / "erc_solar_physics_expenses.xlsx"
    try:
        grants = json.loads(GRANTS_FILE.read_text(encoding="utf-8")).get("grants", []) if GRANTS_FILE.exists() else []
        budget = grants[0].get("total_budget_eur", 0) if grants else 0
        end_date_str = grants[0].get("end_date", "") if grants else ""

        df = _pd.read_excel(xls) if xls.exists() else _pd.DataFrame()
        total_spent = float(df["Amount_EUR"].sum()) if "Amount_EUR" in df.columns else 0.0
        remaining = budget - total_spent

        lines = ["📊 **Budget Summary (computed directly from records)**\n"]
        if budget:
            lines.append(f"- Total budget: EUR {budget:,.0f}")
        lines.append(f"- Spent to date: EUR {total_spent:,.0f}")
        if budget:
            pct = total_spent / budget * 100
            lines.append(f"- Remaining: EUR {remaining:,.0f} ({100 - pct:.1f}%)")

        if "Category" in df.columns and not df.empty:
            lines.append("\nBy category:")
            for cat, amt in df.groupby("Category")["Amount_EUR"].sum().sort_values(ascending=False).items():
                lines.append(f"  • {cat}: EUR {amt:,.0f}")

        if "Date" in df.columns and not df.empty:
            df["_m"] = _pd.to_datetime(df["Date"], errors="coerce").dt.to_period("M")
            monthly = df.groupby("_m")["Amount_EUR"].sum().sort_index()
            if len(monthly) >= 2:
                avg_monthly = float(monthly.mean())
                lines.append(f"\n- Avg monthly burn rate: EUR {avg_monthly:,.0f}")
                if end_date_str:
                    months_left = max((datetime.strptime(end_date_str, "%Y-%m-%d") - datetime.today()).days / 30, 0)
                    projected = total_spent + avg_monthly * months_left
                    lines.append(f"- Projected total spend: EUR {projected:,.0f}")
                    if budget:
                        status = "✅ On track" if projected <= budget else "⚠️ Over budget"
                        lines.append(f"- Assessment: {status}")

        return "\n".join(lines)
    except Exception as e:
        return f"Could not compute budget summary: {e}"


def _lab_notes() -> str:
    lab_dir = PROJECT_ROOT / "lab_notes"
    if not lab_dir.exists():
        return "(No lab notes)"
    entries = []
    for f in sorted(lab_dir.iterdir()):
        if f.suffix in {".md", ".txt"}:
            entries.append(f.read_text(encoding="utf-8", errors="replace"))
    return "\n\n".join(entries[:5])[:3000] if entries else "(No lab notes found)"


@budget_forecaster.on_message(model=TaskRequest)
async def handle_forecast_request(ctx: Context, sender: str, msg: TaskRequest):
    ctx.logger.info(f"[budget_forecaster] query: {msg.query[:60]}…")

    expense_data = _expense_summary()
    grant_ctx    = _grant_context()
    lab_notes    = _lab_notes()

    try:
        resp = llm.chat.completions.create(
            model=VENICE_MODEL,
            max_tokens=2000,
            messages=[
                {"role": "system", "content": (
                    "You are a financial analyst for a research team. "
                    "Forecast remaining ERC grant budget consumption. "
                    "Use expense history for burn rate, lab notes for planned purchase signals. "
                    "Structure your answer: (1) current burn rate, (2) signals from lab notes, "
                    "(3) projected spend by category, (4) on-track assessment, (5) risk flags. "
                    "Be specific with numbers.\n\n"
                    f"GRANT DETAILS:\n{grant_ctx}\n\n"
                    f"EXPENSE HISTORY:\n{expense_data}\n\n"
                    f"LAB NOTES:\n{lab_notes}"
                )},
                {"role": "user", "content": msg.query},
            ],
        )
        result = (resp.choices[0].message.content or "").strip()
        if not result:
            ctx.logger.warning("[budget_forecaster] LLM returned empty — using computed fallback")
            result = _compute_forecast_fallback()
    except Exception as e:
        ctx.logger.warning(f"[budget_forecaster] LLM error ({e}) — using computed fallback")
        result = _compute_forecast_fallback()

    await ctx.send(sender, WorkerResponse(request_id=msg.id, data=result))


if __name__ == "__main__":
    budget_forecaster.run()
