"""
email_drafter.py — Email drafting worker agent.

Generates professional grant-related emails (ERC officer updates, collaboration
requests, recruitment, budget amendment requests) using grant and milestone context.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json
from openai import OpenAI
from uagents import Agent, Context

from models import TaskRequest, WorkerResponse
from config import (
    VENICE_API_KEY, VENICE_BASE_URL, VENICE_MODEL,
    AGENT_SEEDS, AGENT_PORTS,
    KNOWLEDGE_BASE, GRANTS_FILE,
)

llm = OpenAI(base_url=VENICE_BASE_URL, api_key=VENICE_API_KEY)

email_drafter = Agent(
    name="EmailDrafter",
    port=AGENT_PORTS["email_drafter"],
    seed=AGENT_SEEDS["email_drafter"],
    endpoint=[f"http://127.0.0.1:{AGENT_PORTS['email_drafter']}/submit"],
    mailbox=True,
)


def _build_context() -> tuple[str, str]:
    """Return (context_block, pi_name) from grants.json and expense spreadsheet."""
    pi_name    = ""
    ctx_lines: list[str] = []

    if GRANTS_FILE.exists():
        try:
            grants = json.loads(GRANTS_FILE.read_text(encoding="utf-8")).get("grants", [])
            for g in grants:
                if not pi_name:
                    pi_name = g.get("pi", "")
                ctx_lines.append(f"Grant: {g['title']} ({g['id']})")
                ctx_lines.append(f"Funder: {g['funder']} | PI: {g.get('pi', '')}")
                ctx_lines.append(f"Period: {g['start_date']} to {g['end_date']}")
                ctx_lines.append(f"Budget: EUR {g.get('total_budget_eur', 0):,} | Status: {g.get('status', '')}")
                for m in g.get("milestones", []):
                    ctx_lines.append(
                        f"  Milestone {m.get('id')}: {m.get('title')} "
                        f"[{m.get('status','planned')}] due {m.get('due_date','')}"
                    )
        except Exception:
            pass

    xls = KNOWLEDGE_BASE / "erc_solar_physics_expenses.xlsx"
    if xls.exists():
        try:
            import pandas as _pd
            df = _pd.read_excel(xls)
            if "Amount_EUR" in df.columns:
                spent = float(df["Amount_EUR"].sum())
                if grants:
                    budget = grants[0].get("total_budget_eur", 0)
                    ctx_lines.append(
                        f"Spent so far: EUR {spent:,.0f} of EUR {budget:,} "
                        f"(EUR {budget - spent:,.0f} remaining)"
                    )
            # Include full expense table so drafter can use actual names/amounts
            ctx_lines.append("\nFULL EXPENSE RECORDS:")
            ctx_lines.append(df.to_string(index=False))
        except Exception:
            pass

    return "\n".join(ctx_lines), pi_name


@email_drafter.on_message(model=TaskRequest)
async def handle_email_request(ctx: Context, sender: str, msg: TaskRequest):
    ctx.logger.info(f"[email_drafter] query: {msg.query[:60]}…")

    context_block, pi_name = _build_context()

    try:
        _r = llm.chat.completions.create(
            model=VENICE_MODEL,
            max_tokens=1500,
            messages=[
                {"role": "system", "content": (
                    "You are a professional scientific communications assistant. "
                    "Write a professional email based on the request. "
                    "Output ONLY the email — no preamble or commentary. "
                    "First line: Subject: <subject>\n\n"
                    "Then the email body with salutation and sign-off. "
                    f"Sign off as: {pi_name or 'the PI'}\n\n"
                    f"GRANT CONTEXT:\n{context_block}"
                )},
                {"role": "user", "content": msg.query},
            ],
        )
        response = (_r.choices[0].message.content or "").strip()
    except Exception as e:
        response = f"⚠️ Email drafter error: {e}"

    await ctx.send(sender, WorkerResponse(request_id=msg.id, data=response))


if __name__ == "__main__":
    email_drafter.run()
