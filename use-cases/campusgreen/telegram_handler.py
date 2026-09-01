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
import re
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

    # ------------------------------------------------------------------
    # Markdown → Telegram HTML formatting
    # ------------------------------------------------------------------

    async def _send_message(self, chat_id: int, text: str, parse_mode: str = None, reply_markup: dict = None):
        """Override to auto-convert Markdown in agent replies to Telegram HTML.

        When no explicit parse_mode is requested and the text contains Markdown
        formatting characters, convert to HTML before sending. Falls back to
        plain text if the Telegram API rejects the HTML.
        """
        if parse_mode is None and _looks_like_markdown(text):
            html_text = _markdown_to_telegram_html(text)
            try:
                return await super()._send_message(chat_id, html_text, parse_mode="HTML", reply_markup=reply_markup)
            except Exception:
                self._log.warning("HTML send failed, falling back to plain text")
                # Fall through to plain text send below

        return await super()._send_message(chat_id, text, parse_mode=parse_mode, reply_markup=reply_markup)


def _looks_like_markdown(text: str) -> bool:
    """Quick check whether text contains Markdown formatting worth converting."""
    return bool(re.search(r"\*\*|__|\*[^*]+\*|`|~~|^[ \t]*- ", text, re.MULTILINE))


def _escape_html(text: str) -> str:
    """Escape HTML special characters in text (but not inside tags we generate)."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _markdown_to_telegram_html(text: str) -> str:
    """Convert common Markdown formatting to Telegram-compatible HTML.

    Handles: **bold**, *italic*, `inline code`, ```code blocks```,
    and Markdown bullet lists (- item).
    """
    # Step 1: Extract code blocks and inline code to protect them from further processing
    placeholders: list[str] = []

    def _save_code_block(m: re.Match) -> str:
        # Remove optional language hint on first line
        code = m.group(1)
        lines = code.split("\n")
        if lines and lines[0].strip() and not any(c in lines[0] for c in " \t=({"):
            lines = lines[1:]
        content = _escape_html("\n".join(lines).strip())
        placeholders.append(f"<pre>{content}</pre>")
        return f"\x00PLACEHOLDER{len(placeholders) - 1}\x00"

    def _save_inline_code(m: re.Match) -> str:
        content = _escape_html(m.group(1))
        placeholders.append(f"<code>{content}</code>")
        return f"\x00PLACEHOLDER{len(placeholders) - 1}\x00"

    # Fenced code blocks: ```...```
    result = re.sub(r"```(?:\w*\n)?(.*?)```", _save_code_block, text, flags=re.DOTALL)
    # Inline code: `...`
    result = re.sub(r"`([^`]+)`", _save_inline_code, result)

    # Step 2: Escape remaining HTML special characters
    result = _escape_html(result)

    # Step 3: Apply formatting conversions
    # Bold: **text** or __text__
    result = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", result)
    result = re.sub(r"__(.+?)__", r"<b>\1</b>", result)
    # Italic: *text* or _text_ (but not inside words with underscores)
    result = re.sub(r"(?<!\w)\*([^*]+?)\*(?!\w)", r"<i>\1</i>", result)
    result = re.sub(r"(?<!\w)_([^_]+?)_(?!\w)", r"<i>\1</i>", result)
    # Strikethrough: ~~text~~
    result = re.sub(r"~~(.+?)~~", r"<s>\1</s>", result)

    # Step 4: Convert Markdown bullet lists (- item) to • item
    result = re.sub(r"(?m)^[ \t]*[-*][ \t]+", "• ", result)

    # Step 5: Restore code placeholders
    for i, placeholder in enumerate(placeholders):
        result = result.replace(f"\x00PLACEHOLDER{i}\x00", placeholder)

    return result.strip()


