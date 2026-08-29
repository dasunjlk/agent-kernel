"""CampusGreen WhatsApp server (Agent Kernel messaging integration).

Entry point that serves CampusGreen over WhatsApp through Agent Kernel's native
``AgentWhatsAppRequestHandler``, mounted on the REST API. Incoming WhatsApp
webhooks reach the CampusGreen agent (see ``agent.py``) and its tools, and the
agent's replies are sent back through WhatsApp.

Run from this directory (webhook credentials must be configured, and the server
needs a public HTTPS URL such as an ngrok or pinggy tunnel to receive WhatsApp's
webhook):

.. code-block:: bash

    uv run python server.py

Configuration is read from environment variables / ``config.yaml``:
``AK_WHATSAPP__ACCESS_TOKEN``, ``AK_WHATSAPP__PHONE_NUMBER_ID``,
``AK_WHATSAPP__VERIFY_TOKEN``, ``AK_WHATSAPP__APP_SECRET`` and
``OPENAI_API_KEY``. See INTEGRATION.md for the full setup.

Missing required variables produce a clear startup error (names only, never
values) instead of an obscure stack trace. ``validate_config`` is importable
so tests can exercise the check without starting the server.
"""

import os
import sys

from agentkernel.api import RESTAPI
from agentkernel.openai import OpenAIModule
from agentkernel.whatsapp import AgentWhatsAppRequestHandler

from agent import AGENTS

OpenAIModule(AGENTS)

REQUIRED_ENV_VARS = [
    "OPENAI_API_KEY",
    "AK_WHATSAPP__ACCESS_TOKEN",
    "AK_WHATSAPP__PHONE_NUMBER_ID",
    "AK_WHATSAPP__VERIFY_TOKEN",
]


def validate_config(environ: dict | None = None) -> list[str]:
    """Return the names of required environment variables that are missing.

    Never exposes values; used at startup and by tests to verify the failure
    is clear and safe.
    """
    env = os.environ if environ is None else environ
    return [name for name in REQUIRED_ENV_VARS if not (env.get(name) or "").strip()]


def main() -> None:
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
    RESTAPI.run([AgentWhatsAppRequestHandler()])


if __name__ == "__main__":
    main()
