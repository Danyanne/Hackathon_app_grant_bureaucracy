from uagents import Agent, Context
from uagents_core.contrib.protocols.chat import ChatMessage, TextContent

# Minimalist trigger: distinct port so it doesn't collide with the Orchestrator's 8000
user = Agent(name="UserTrigger", port=8003, endpoint=["http://127.0.0.1:8003/submit"], network="testnet")

@user.on_event("startup")
async def send_msg(ctx: Context):
    orchestrator_address = "agent1q05laskh2fqxf27vm8t9etrp3x3rnsa5kxdds3g6zy44v9w05fl0uze76e6" # Orchestrator's address (derived from its seed)

    ctx.logger.info("Firing ChatMessage to Orchestrator...")
    await ctx.send(orchestrator_address, ChatMessage(
        content=[TextContent(text="Please run an expense audit")]
    ))
    ctx.logger.info("Request sent. The agent will stay active to receive replies.")
    # DO NOT use exit(0) here!

@user.on_message(model=ChatMessage)
async def handle_reply(ctx: Context, sender: str, msg: ChatMessage):
    text = next(c.text for c in msg.content if isinstance(c, TextContent))
    ctx.logger.info(f"Received reply from Orchestrator:\n{text}")

if __name__ == "__main__":
    user.run()