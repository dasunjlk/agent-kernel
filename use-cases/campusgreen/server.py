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
"""

from agentkernel.api import RESTAPI
from agentkernel.openai import OpenAIModule
from agentkernel.whatsapp import AgentWhatsAppRequestHandler

from agent import AGENTS

OpenAIModule(AGENTS)


if __name__ == "__main__":
    RESTAPI.run([AgentWhatsAppRequestHandler()])
