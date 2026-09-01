"""Local demo driver for the CampusGreen Telegram integration.

This runs the SAME Agent Kernel messaging path as the real ``server.py`` —
Telegram-style webhook updates routed through ``CampusGreenTelegramHandler``
(the thin CampusGreen shim over ``AgentTelegramRequestHandler``, see
``telegram_handler.py``) into the CampusGreen agent and its tools — but
intercepts the outbound `_send_message` call so it can run locally with **no
Telegram bot token** and no network access to Telegram servers.

It demonstrates, end to end:

1. A user sends /start to introduce the bot.
2. A user reports an issue.
3. The agent identifies the location.
4. The agent creates the issue.
5. The agent notifies the responsible team.
6. The user asks for status.
7. The agent retrieves the issue.
8. The user reports a worsening condition.
9. The agent escalates (update + notify).
10. The user asks for an action plan.
11. The agent gathers recorded issues (report + search) and prioritizes.
12. The user asks why a plan item ranks first; the agent explains the evidence.
13. The user asks the agent to act on the plan; the agent escalates the top
    unresolved issue and notifies the responsible team.

Because a second sender chat_id is used, the demo also shows per-user session
isolation (each Telegram chat gets its own CampusGreen session).

Requires an ``OPENAI_API_KEY`` (or ``GROQ_API_KEY``) for the agent's real
tool-calling reasoning. Without one, the demo prints how to enable it and exits.

Run from this directory::

    uv run python integration_demo.py
"""

from __future__ import annotations

import asyncio
import os
import sys

from agentkernel.openai import OpenAIModule
from fastapi import FastAPI

from telegram_handler import CampusGreenTelegramHandler

SEQUENCE: list[tuple[int, str]] = [
    (15550000001, "/start"),
    (15550000001, "There's a water leak outside Lab 3."),
    (15550000001, "It's getting worse — water is spreading across the floor near the chemistry block."),
    (15550000001, "What's the status of WTR-001?"),
    (15550000002, "The bins near the Student Cafe are overflowing."),
    (15550000001, "What are the biggest sustainability problems this month?"),
    (15550000001, "What should we prioritize to improve sustainability this month?"),
    (15550000001, "Why is ENERGY ranked first?"),
    (15550000001, "Escalate the top unresolved energy issue."),
]


class LocalTelegramHandler(CampusGreenTelegramHandler):
    """Handler whose outbound sends are printed locally instead of hitting Telegram."""

    def __init__(self) -> None:
        super().__init__()
        self.transcript: list[str] = []

    async def _send_message(self, chat_id: int, text: str, parse_mode: str = None, reply_markup: dict = None):
        line = f"[Telegram -> {chat_id}] {text}"
        print(line)
        self.transcript.append(line)


def _install_placeholder_credentials() -> None:
    """Set fake Telegram credentials so the handler constructs without real tokens.

    These are only used to satisfy the handler's constructor/URL building; no
    network call is made because this demo intercepts `_send_message`.
    """
    os.environ.setdefault("TELEGRAM_BOT_TOKEN", "demo-placeholder-token")
    os.environ.setdefault("AK_TELEGRAM__BOT_TOKEN", "demo-placeholder-token")


async def _run() -> None:
    has_llm = bool((os.environ.get("OPENAI_API_KEY") or "").strip() or (os.environ.get("GROQ_API_KEY") or "").strip())
    if not has_llm:
        print("OPENAI_API_KEY (or GROQ_API_KEY) is not set. Set it to let the CampusGreen agent reason and call tools.")
        return

    _install_placeholder_credentials()

    from agent import AGENTS

    OpenAIModule(AGENTS)

    handler = LocalTelegramHandler()
    app = FastAPI(title="CampusGreen Telegram (local demo)")
    app.include_router(handler.get_router())

    import httpx

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        for index, (sender_id, message_text) in enumerate(SEQUENCE, start=1):
            print(f"\n{'=' * 72}")
            print(f"Step {index} | chat_id={sender_id} | message: {message_text!r}")
            print("-" * 72)
            payload = {
                "update_id": 1000 + index,
                "message": {
                    "message_id": index,
                    "from": {"id": sender_id, "first_name": "Student", "is_bot": False},
                    "chat": {"id": sender_id, "type": "private"},
                    "date": 1700000000 + index,
                    "text": message_text,
                },
            }
            resp = await client.post("/telegram/webhook", json=payload)
            resp.raise_for_status()

    print(f"\n{'=' * 72}")
    print("Demo complete. All outbound responses were captured by the local handler.")
    print(
        "To receive real Telegram messages instead, run `uv run python server.py` with "
        "a real TELEGRAM_BOT_TOKEN — see INTEGRATION.md."
    )


if __name__ == "__main__":
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        sys.exit(0)
