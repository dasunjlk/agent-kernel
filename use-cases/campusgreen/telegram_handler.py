"""CampusGreen Telegram boundary shim.

Phase 9A messaging integration for Telegram using Agent Kernel's
``AgentTelegramRequestHandler``.

Provides:
- **Message normalization**: Text message bodies are stripped of surrounding
  whitespace so the agent receives a clean prompt, and whitespace-only/empty
  messages are ignored.
- **Duplicate platform-event handling**: Telegram update and message IDs are
  tracked in a bounded in-memory set (_DEDUP_MAX = 10000) to prevent reprocessing
  duplicate deliveries.
- **CampusGreen /start greeting**: Friendly introduction explaining capabilities
  (reporting leaks/waste, checking ticket status, action planning).
- **Long polling transport**: Built-in ``poll()`` runner for local development
  without needing an HTTPS tunnel or webhook configuration.
- **Session & Identity mapping**: Telegram ``chat_id`` is mapped to Agent Kernel
  ``session_id`` (preserving isolated state per chat), and user ID is mapped to
  ``user_id`` (populating ``ak.acting_user_id``).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
from agentkernel.telegram import AgentTelegramRequestHandler

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

_DEDUP_MAX = 10000

START_MESSAGE = (
    "👋 Welcome to CampusGreen! I'm your campus sustainability assistant.\n\n"
    "You can report sustainability issues (such as water leaks, energy waste, or bin overflow), "
    "check the status of existing tickets, or request sustainability action plans.\n\n"
    "How can I help you today?"
)

HELP_MESSAGE = (
    "CampusGreen Sustainability Assistant\n\n"
    "Available commands:\n"
    "/start - Introduce CampusGreen and show capabilities\n"
    "/help  - Show this help message\n\n"
    "Or send any text message describing an issue or asking a question."
)


class CampusGreenTelegramHandler(AgentTelegramRequestHandler):
    """Adds message normalization, duplicate-event guards, /start behavior, and polling."""

    def __init__(self) -> None:
        import os
        from agentkernel.core import Config

        token = (os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("AK_TELEGRAM__BOT_TOKEN") or "").strip()
        if token:
            os.environ["AK_TELEGRAM__BOT_TOKEN"] = token
            os.environ["TELEGRAM_BOT_TOKEN"] = token
            Config._reset()

        super().__init__()
        self._log = logging.getLogger("ak.api.telegram.campusgreen")
        self._seen_update_ids: set[str] = set()
        self._seen_message_ids: set[str] = set()

    def _is_duplicate_update(self, update_id: Any) -> bool:
        if update_id is None:
            return False
        return str(update_id) in self._seen_update_ids

    def _track_update(self, update_id: Any) -> None:
        if update_id is None:
            return
        self._seen_update_ids.add(str(update_id))
        if len(self._seen_update_ids) > _DEDUP_MAX:
            self._seen_update_ids.pop()

    def _is_duplicate_message(self, message_id: Any) -> bool:
        if message_id is None:
            return False
        return str(message_id) in self._seen_message_ids

    def _track_message(self, message_id: Any) -> None:
        if message_id is None:
            return
        self._seen_message_ids.add(str(message_id))
        if len(self._seen_message_ids) > _DEDUP_MAX:
            self._seen_message_ids.pop()

    async def _process_webhook_body(self, body: dict) -> None:
        update_id = body.get("update_id")
        if self._is_duplicate_update(update_id):
            self._log.warning(f"duplicate_update_skipped update_id={update_id}")
            return
        self._track_update(update_id)
        await super()._process_webhook_body(body)

    async def _handle_command(self, chat_id: int, command: str) -> None:
        self._log.debug(f"Processing command: {command}")
        if command == "/start":
            await self._send_message(chat_id, START_MESSAGE)
        elif command == "/help":
            await self._send_message(chat_id, HELP_MESSAGE)
        else:
            await self._process_agent_message(chat_id, command)

    async def _handle_message(self, message: dict, *args: Any, **kwargs: Any) -> None:
        message_id = message.get("message_id")
        if self._is_duplicate_message(message_id):
            self._log.warning(f"duplicate_message_skipped message_id={message_id}")
            return

        text = message.get("text")
        caption = message.get("caption")
        has_files = "document" in message
        has_images = "photo" in message

        if text is not None:
            stripped = text.strip()
            if not stripped and not has_files and not has_images:
                self._log.warning("message_ignored empty_text")
                return
            message["text"] = stripped
        elif caption is not None:
            message["caption"] = caption.strip()

        self._track_message(message_id)
        await super()._handle_message(message)

    async def poll(self, timeout: int = 30, once: bool = False) -> None:
        """Run long polling against the Telegram Bot API."""
        if not self._bot_token:
            raise ValueError("Incomplete Telegram configuration. TELEGRAM_BOT_TOKEN is required.")

        self._log.info("Starting Telegram long polling for CampusGreen...")
        url = f"{self._base_url}/getUpdates"
        offset: int | None = None

        # Increase HTTP timeout so long polling calls (with server-side timeout) don't get terminated early
        client_timeout = float(timeout + 15)

        async with httpx.AsyncClient(timeout=client_timeout) as client:
            while True:
                params: dict[str, Any] = {"timeout": timeout}
                if offset is not None:
                    params["offset"] = offset

                try:
                    response = await client.post(url, json=params)
                    if response.status_code == 200:
                        data = response.json()
                        if data.get("ok"):
                            updates = data.get("result", [])
                            for update in updates:
                                update_id = update.get("update_id")
                                if update_id is not None:
                                    offset = update_id + 1
                                await self._process_webhook_body(update)
                        else:
                            self._log.error(f"Telegram getUpdates returned not ok: {data}")
                    else:
                        self._log.error(f"Telegram getUpdates failed HTTP {response.status_code}: {response.text}")
                except asyncio.CancelledError:
                    self._log.info("Telegram polling cancelled.")
                    break
                except Exception as e:
                    self._log.error(f"Error during Telegram polling: {e}")
                    await asyncio.sleep(2)

                if once:
                    break
