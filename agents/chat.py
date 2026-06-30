import threading
import queue
import sys
import itertools
import logging
from uagents import Agent, Context
from uagents_core.contrib.protocols.chat import ChatMessage, TextContent

# Silence all internal agent/network logs so they don't pollute the chat UI
for _noisy in ("uagents", "uagents.registration", "httpx", "uvicorn",
               "uvicorn.error", "uvicorn.access"):
    logging.getLogger(_noisy).setLevel(logging.CRITICAL)

ORCHESTRATOR_ADDRESS = "agent1q05laskh2fqxf27vm8t9etrp3x3rnsa5kxdds3g6zy44v9w05fl0uze76e6"

user = Agent(name="ChatUser", port=8003, endpoint=["http://127.0.0.1:8003/submit"], network="testnet")

_msg_queue: queue.Queue = queue.Queue()
_ready_for_input = threading.Event()
_ready_for_input.set()   # start in input-ready state
_waiting = False
_spinner = itertools.cycle(['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'])


def _stdin_reader() -> None:
    """Background thread: read lines from stdin, block until the previous reply arrives."""
    while True:
        _ready_for_input.wait()          # wait for reply before accepting next message
        try:
            line = input("You: ")
            if line.strip():
                _ready_for_input.clear() # lock input until reply comes back
                _msg_queue.put(line.strip())
        except EOFError:
            break


@user.on_event("startup")
async def on_startup(ctx: Context) -> None:
    print("\n=== Grant Bureaucracy Assistant ===")
    print("Type your question and press Enter. Ctrl+C to quit.\n")
    threading.Thread(target=_stdin_reader, daemon=True).start()


@user.on_interval(period=0.5)
async def dispatch_pending(ctx: Context) -> None:
    global _waiting
    while not _msg_queue.empty():
        text = _msg_queue.get_nowait()
        await ctx.send(ORCHESTRATOR_ADDRESS, ChatMessage(
            content=[TextContent(text=text)]
        ))
        _waiting = True


@user.on_interval(period=0.15)
async def spin(ctx: Context) -> None:
    if _waiting:
        sys.stdout.write(f'\r{next(_spinner)} Thinking...')
        sys.stdout.flush()


@user.on_message(model=ChatMessage)
async def handle_reply(ctx: Context, sender: str, msg: ChatMessage) -> None:
    global _waiting
    _waiting = False
    text = next(c.text for c in msg.content if isinstance(c, TextContent))
    sys.stdout.write('\r' + ' ' * 20 + '\r')  # clear spinner line
    print(f"Assistant:\n{text}\n")
    _ready_for_input.set()               # unlock input for next question


if __name__ == "__main__":
    user.run()
