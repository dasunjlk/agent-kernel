"""Local demo driver for the CampusGreen WhatsApp integration.

This runs the SAME Agent Kernel messaging path as the real ``server.py`` —
WhatsApp-style webhook payloads routed through ``CampusGreenWhatsAppHandler``
(the thin CampusGreen shim over ``AgentWhatsAppRequestHandler``, see
``whatsapp_handler.py``) into the CampusGreen agent and its tools — but
intercepts the outbound `_send_message` call so it can run locally with **no
Meta/WhatsApp credentials** and no network access to WhatsApp.

It demonstrates, end to end:

1. A user reports an issue.
2. The agent identifies the location.
3. The agent creates the issue.
4. The agent notifies the responsible team.
5. The user asks for status.
6. The agent retrieves the issue.
7. The user reports a worsening condition.
8. The agent escalates (update + notify).
9. The user asks for an action plan.
10. The agent gathers the recorded issues (report + search) and prioritizes.
11. The user asks why a plan item ranks first; the agent explains the evidence.
12. The user asks the agent to act on the plan; the agent escalates the top
    unresolved issue and notifies the responsible team.

Because a second sender number is used, the demo also shows per-user session
isolation (each WhatsApp sender gets its own CampusGreen session).

Requires an ``OPENAI_API_KEY`` for the agent's real tool-calling reasoning
(and it calls the OpenAI API through Agent Kernel). Without one, the demo
prints how to enable it and exits.

Run from this directory::

    uv run python integration_demo.py
"""

from __future__ import annotations

import asyncio
import os
import sys

from agentkernel.openai import OpenAIModule
from fastapi import FastAPI

from whatsapp_handler import CampusGreenWhatsAppHandler

SEQUENCE: list[tuple[str, str]] = [
    ("+15550000001", "There's a water leak outside Lab 3."),
    ("+15550000001", "It's getting worse — water is spreading across the floor near the chemistry block."),
    ("+15550000001", "What's the status of WTR-001?"),
    ("+15550000002", "The bins near the Student Cafe are overflowing."),
    ("+15550000001", "What are the biggest sustainability problems this month?"),
    ("+15550000001", "What should we prioritize to improve sustainability this month?"),
    ("+15550000001", "Why is ENERGY ranked first?"),
    ("+15550000001", "Escalate the top unresolved energy issue."),
]


class LocalWhatsAppHandler(CampusGreenWhatsAppHandler):
    """Handler whose outbound sends are printed locally instead of hitting Meta."""

    def __init__(self) -> None:
        super().__init__()
        self.transcript: list[str] = []

    async def _send_message(self, to_number, text: str, reply_to_message_id=None):
        line = f"[WhatsApp -> {to_number}] {text}"
        print(line)
        self.transcript.append(line)


def _install_placeholder_credentials() -> None:
    """Set fake WhatsApp credentials so the handler constructs without real tokens.

    These are only used to satisfy the handler's constructor/URL building; no
    network call is made because this demo intercepts `_send_message`.
    """
    os.environ.setdefault("AK_WHATSAPP__ACCESS_TOKEN", "demo-placeholder-token")
    os.environ.setdefault("AK_WHATSAPP__PHONE_NUMBER_ID", "demo-phone-number-id")
    os.environ.setdefault("AK_WHATSAPP__VERIFY_TOKEN", "demo-verify-token")


async def _run() -> None:
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY is not set. Set it to let the CampusGreen agent reason and call tools.")
        return

    _install_placeholder_credentials()

    from agent import AGENTS

    OpenAIModule(AGENTS)

    handler = LocalWhatsAppHandler()
    app = FastAPI(title="CampusGreen WhatsApp (local demo)")
    app.include_router(handler.get_router())

    import httpx

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        for index, (sender, message_text) in enumerate(SEQUENCE, start=1):
            print(f"\n{'=' * 72}")
            print(f"Step {index} | sender={sender} | message: {message_text!r}")
            print("-" * 72)
            payload = {
                "object": "whatsapp_business_account",
                "entry": [
                    {
                        "id": "demo-account",
                        "changes": [
                            {
                                "value": {
                                    "messaging_product": "whatsapp",
                                    "metadata": {"display_phone_number": "15551234000", "phone_number_id": "123"},
                                    "contacts": [{"profile": {"name": "Student"}, "wa_id": sender}],
                                    "messages": [
                                        {
                                            "from": sender,
                                            "id": f"wamid.{index}",
                                            "timestamp": str(index),
                                            "type": "text",
                                            "text": {"body": message_text},
                                        }
                                    ],
                                },
                                "field": "messages",
                            }
                        ],
                    }
                ],
            }
            resp = await client.post("/whatsapp/webhook", json=payload)
            resp.raise_for_status()

    print(f"\n{'=' * 72}")
    print("Demo complete. All outbound responses were captured by the local handler.")
    print(
        "To receive real WhatsApp messages instead, run `uv run python server.py` with "
        "real credentials — see INTEGRATION.md."
    )


if __name__ == "__main__":
    import asyncio

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        sys.exit(0)
