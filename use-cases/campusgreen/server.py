"""CampusGreen Telegram server (Agent Kernel messaging integration).

Entry point that serves CampusGreen over Telegram through Agent Kernel's
Telegram integration (wrapped by CampusGreen's boundary shim,
``CampusGreenTelegramHandler`` — see ``telegram_handler.py``). Incoming Telegram
messages reach the CampusGreen agent (see ``agent.py``) and its tools, and the
agent's replies are sent back through Telegram.

By default, the server runs **long polling** (``getUpdates``), which requires no
public HTTPS tunnel (ngrok / pinggy) for local development:

.. code-block:: bash

    uv run python server.py

To run as a webhook server mounted on the REST API instead:

.. code-block:: bash

    uv run python server.py --webhook

Configuration is read from environment variables / ``config.yaml``:
``TELEGRAM_BOT_TOKEN`` (or ``AK_TELEGRAM__BOT_TOKEN``) and ``OPENAI_API_KEY``
(or ``GROQ_API_KEY``). See INTEGRATION.md for the full setup.

Missing required variables produce a clear startup error (names only, never
values) instead of an obscure stack trace. ``validate_config`` is importable
so tests can exercise the check without starting the server.
"""

from __future__ import annotations

import asyncio
import os
import sys

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass




def _sync_env(env: dict) -> None:
    """Ensure TELEGRAM_BOT_TOKEN and AK_TELEGRAM__BOT_TOKEN are synchronized."""
    token = (env.get("TELEGRAM_BOT_TOKEN") or env.get("AK_TELEGRAM__BOT_TOKEN") or "").strip()
    if token:
        env.setdefault("TELEGRAM_BOT_TOKEN", token)
        env.setdefault("AK_TELEGRAM__BOT_TOKEN", token)


_sync_env(os.environ)

from agent import AGENTS
from agentkernel.core import Config

Config._reset()

from agentkernel.openai import OpenAIModule
from telegram_handler import CampusGreenTelegramHandler

OpenAIModule(AGENTS)


def validate_config(environ: dict | None = None) -> list[str]:
    """Return the names of required environment variables that are missing.

    Never exposes values; used at startup and by tests to verify the failure
    is clear and safe.
    """
    env = os.environ if environ is None else environ
    _sync_env(env)
    missing: list[str] = []

    has_llm = bool((env.get("OPENAI_API_KEY") or "").strip() or (env.get("GROQ_API_KEY") or "").strip())
    if not has_llm:
        missing.append("OPENAI_API_KEY")

    has_token = bool((env.get("TELEGRAM_BOT_TOKEN") or "").strip() or (env.get("AK_TELEGRAM__BOT_TOKEN") or "").strip())
    if not has_token:
        missing.append("TELEGRAM_BOT_TOKEN")

    return missing


def main() -> None:
    _sync_env(os.environ)
    missing = validate_config()
    if missing:
        print("Missing required configuration:", file=sys.stderr)
        for name in missing:
            print(f"- {name}", file=sys.stderr)
        print(
            "Set these environment variables (see .env.example and INTEGRATION.md) and try again.",
            file=sys.stderr,
        )
        raise SystemExit(2)

    use_webhook = "--webhook" in sys.argv or os.environ.get("CAMPUSGREEN_TRANSPORT") == "webhook"

    if use_webhook:
        from agentkernel.api import RESTAPI

        print("Starting CampusGreen Telegram webhook server on REST API...")
        RESTAPI.run([CampusGreenTelegramHandler()])
    else:
        print("Starting CampusGreen Telegram long polling runner...")
        handler = CampusGreenTelegramHandler()
        try:
            asyncio.run(handler.poll())
        except KeyboardInterrupt:
            print("\nShutting down Telegram long polling runner.")


if __name__ == "__main__":
    main()
