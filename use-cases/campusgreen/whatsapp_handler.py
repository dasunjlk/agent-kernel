"""CampusGreen WhatsApp boundary shim.

Phase 6 responsibilities that Agent Kernel's ``AgentWhatsAppRequestHandler``
does not provide are implemented here as a thin subclass that sits in front of
the native handler and delegates everything else to it:

- **Message normalization.** Text message bodies are stripped of surrounding
  whitespace so the agent always receives a clean user message, and a
  whitespace-only (or empty) text payload is ignored instead of being passed
  through as a meaningless prompt.
- **Duplicate platform-event handling.** WhatsApp can redeliver the same webhook
  event (with the same message ``id``). A small in-memory set of processed
  message IDs skips an already-seen event, so a redelivered ``wamid`` is not
  reprocessed into a second ticket. This is deliberately a simple per-process
  guard, not a durable or distributed idempotency layer.

Everything else — parsing text/interactive/image/document/audio/video, session
identity (``session_id = sender number``), routing into the CampusGreen agent,
error mapping, long-message splitting, webhook signature verification, and
outbound sending — is inherited unchanged from the Agent Kernel handler.
"""

from __future__ import annotations

import logging

from agentkernel.whatsapp import AgentWhatsAppRequestHandler

# Bound the seen-message-ID set so a long-lived process never grows without
# limit. This is a local de-duplication guard (Phase 6 section 15), not a
# general-purpose idempotency or rate-limiting service (sections 15 / 26).
_DEDUP_MAX = 10000


class CampusGreenWhatsAppHandler(AgentWhatsAppRequestHandler):
    """Adds message normalization and duplicate-event guards to WhatsApp handling."""

    def __init__(self) -> None:
        super().__init__()
        self._log = logging.getLogger("ak.api.whatsapp.campusgreen")
        self._seen_message_ids: set[str] = set()

    def _normalized_text(self, message: dict) -> str | None:
        """Return the stripped text of a text message, or None if not text.

        Mirrors the native handler's extraction for ``type == "text"`` only;
        other message types are left untouched for the native handler to parse.
        """
        if message.get("type") != "text":
            return None
        raw = message.get("text", {}).get("body")
        if not isinstance(raw, str):
            return None
        return raw.strip()

    def _is_duplicate(self, message: dict) -> bool:
        message_id = message.get("id")
        if not message_id:
            return False
        return message_id in self._seen_message_ids

    def _track(self, message: dict) -> None:
        message_id = message.get("id")
        if not message_id:
            return
        self._seen_message_ids.add(str(message_id))
        if len(self._seen_message_ids) > _DEDUP_MAX:
            # Drop an arbitrary older entry to keep the set bounded.
            self._seen_message_ids.pop()

    async def _handle_message(self, message: dict, value: dict) -> None:
        if self._is_duplicate(message):
            self._log.warning(f"duplicate_event_skipped message_id={message.get('id')}")
            return

        stripped = self._normalized_text(message)
        if stripped is not None:
            if not stripped:
                self._log.warning("message_ignored empty_text")
                return
            # Hand the agent a clean message by normalizing the body in place.
            message["text"] = {"body": stripped}

        self._track(message)
        await super()._handle_message(message, value)
