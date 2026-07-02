"""
report_writer.py — Research progress report writer worker agent.

Generates structured ERC progress reports and answers research questions
by drawing on lab notes, GitHub commits, and paper drafts.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from uagents import Agent, Context

from models import TaskRequest, WorkerResponse
from config import AGENT_SEEDS, AGENT_PORTS

report_writer = Agent(
    name="ReportWriter",
    port=AGENT_PORTS["report_writer"],
    seed=AGENT_SEEDS["report_writer"],
    endpoint=[f"http://127.0.0.1:{AGENT_PORTS['report_writer']}/submit"],
    mailbox=True,
)


@report_writer.on_message(model=TaskRequest)
async def handle_report_request(ctx: Context, sender: str, msg: TaskRequest):
    ctx.logger.info(f"[report_writer] query: {msg.query[:60]}…")
    try:
        from worker_logic import generate_report_response
        report_text, saved_path = generate_report_response(msg.query)
        if saved_path:
            rel      = saved_path.split("Hackathon_app_grant_bureaucracy/")[-1]
            response = f"✅ Report saved to `{rel}`\n\n---\n\n{report_text}"
        else:
            response = report_text
    except Exception as e:
        response = f"⚠️ Report writer error: {e}"

    await ctx.send(sender, WorkerResponse(request_id=msg.id, data=response))


if __name__ == "__main__":
    report_writer.run()
