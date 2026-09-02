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
import random
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
    "Type /help to see all available commands, or just send a message describing your issue!"
)

HELP_MESSAGE = (
    "🌿 <b>CampusGreen Sustainability Assistant</b>\n\n"
    "<b>Commands:</b>\n"
    "/start — Welcome message\n"
    "/help — Show this help\n"
    "/status <code>&lt;ID&gt;</code> — Check issue status (e.g. <code>/status WTR-001</code>)\n"
    "/myissues — List all issues you reported\n"
    "/dashboard — Sustainability summary & open issues\n"
    "/categories — Show all issue categories\n"
    "/tips — Get a sustainability tip\n"
    "/feedback <code>&lt;ID&gt; &lt;message&gt;</code> — Add a note to an issue\n\n"
    "Or just send any text message describing an issue or asking a question."
)

SUSTAINABILITY_TIPS = [
    "💧 Report dripping taps immediately — a single drip can waste over 11,000 litres a year!",
    "💡 Switch off lights and monitors when you leave a room. It takes just a second!",
    "♻️ Use the correct recycling bins — contamination means the whole bin goes to landfill.",
    "🚶 Walk or cycle to campus when you can. Short car trips have a disproportionate carbon impact.",
    "🌡️ If a room feels too hot or cold, report it instead of opening windows while heating is on.",
    "🍽️ Bring a reusable container to the café — reduce single-use packaging waste on campus.",
    "🔌 Unplug chargers when not in use. They still draw power even when nothing is connected.",
    "📦 Flatten cardboard boxes before recycling — it saves space and helps collection teams.",
    "🚰 Carry a refillable water bottle. Campus water fountains are free and reduce plastic waste.",
    "🌳 Respect green spaces — don't litter, and report any damage to plants or trees.",
    "🧪 Dispose of lab chemicals properly — never pour them down the drain.",
    "📱 Use CampusGreen to report issues! The faster we know, the faster we fix.",
    "🏢 Close fume-hood sashes when not in use — open sashes waste huge amounts of energy.",
    "🖨️ Print double-sided and only when you really need a hard copy.",
    "🌍 Join a campus sustainability group — collective action has the biggest impact!",
]

CATEGORY_DESCRIPTIONS = {
    "WATER": "💧 Leaks, flooding, dripping taps, irrigation issues",
    "ENERGY": "💡 Wasted electricity, faulty lighting, HVAC problems",
    "WASTE": "🗑️ Overflowing bins, improper disposal, recycling issues",
    "FOOD": "🍽️ Food waste, unsafe storage, cafeteria hygiene",
    "POLLUTION": "🏭 Air quality, noise, chemical spills, odours",
    "INFRASTRUCTURE": "🏗️ Broken fixtures, damaged paths, facility maintenance",
    "OTHER": "📋 Anything that doesn't fit the above categories",
}

_PRIORITY_EMOJI = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "🟢"}


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
        parts = command.strip().split(None, 1)
        cmd = parts[0].lower().split("@")[0]  # strip @botname suffix
        args = parts[1].strip() if len(parts) > 1 else ""

        if cmd == "/start":
            await self._send_message(chat_id, START_MESSAGE)
        elif cmd == "/help":
            await self._send_message(chat_id, HELP_MESSAGE, parse_mode="HTML")
        elif cmd == "/status":
            await self._cmd_status(chat_id, args)
        elif cmd == "/myissues":
            await self._cmd_myissues(chat_id)
        elif cmd == "/dashboard":
            await self._cmd_dashboard(chat_id)
        elif cmd == "/categories":
            await self._cmd_categories(chat_id)
        elif cmd in ("/tips", "/tip"):
            await self._cmd_tips(chat_id)
        elif cmd == "/feedback":
            await self._cmd_feedback(chat_id, args)
        else:
            await self._process_agent_message(chat_id, command)

    # ------------------------------------------------------------------
    # Slash command handlers
    # ------------------------------------------------------------------

    async def _cmd_status(self, chat_id: int, args: str) -> None:
        """Handle /status <issue_id>."""
        from tool import _issue_store, _present_issue, _validate_issue_id

        if not args:
            await self._send_message(
                chat_id,
                "⚠️ Please provide an issue ID.\nExample: <code>/status WTR-001</code>",
                parse_mode="HTML",
            )
            return

        issue_id = _validate_issue_id(args)
        if issue_id is None:
            await self._send_message(
                chat_id,
                f"⚠️ <code>{_escape_html(args)}</code> is not a valid issue ID.\nExpected format: <code>WTR-001</code>",
                parse_mode="HTML",
            )
            return

        issue = _issue_store().get(issue_id)
        if issue is None:
            await self._send_message(chat_id, f"❌ No issue found with ID <code>{issue_id}</code>.", parse_mode="HTML")
            return

        presented = _present_issue(issue)
        emoji = _PRIORITY_EMOJI.get(presented["priority"], "⚪")
        lines = [
            f"📋 <b>Issue {presented['issue_id']}</b>",
            "",
            f"<b>Category:</b> {presented['category']}",
            f"<b>Location:</b> {_escape_html(presented['location'])}",
            f"<b>Priority:</b> {emoji} {presented['priority']}",
            f"<b>Status:</b> {presented['status']}",
            f"<b>Team:</b> {_escape_html(presented['assigned_team'])}",
            f"<b>Reported by:</b> {_escape_html(presented['reported_by'])}",
            f"<b>Created:</b> {presented['created_at']}",
            f"<b>Updated:</b> {presented['updated_at']}",
            "",
            f"<b>Description:</b> {_escape_html(presented['description'])}",
        ]
        if presented.get("history"):
            lines.append("")
            lines.append("<b>History:</b>")
            for entry in presented["history"][-5:]:
                lines.append(f"  • [{entry.get('timestamp', '')}] {entry.get('event', '')}: {_escape_html(entry.get('note', ''))}")

        await self._send_message(chat_id, "\n".join(lines), parse_mode="HTML")

    async def _cmd_myissues(self, chat_id: int) -> None:
        """Handle /myissues — list issues reported by this Telegram user."""
        from tool import _issue_store, _location_by_id

        user_id = str(chat_id)
        all_issues = _issue_store().all()
        my_issues = [
            issue for issue in all_issues
            if str(issue.get("reported_by", "")) == user_id
            or str(issue.get("source_channel", "")) == "telegram"
        ]

        if not my_issues:
            await self._send_message(chat_id, "📭 You haven't reported any issues yet.\nJust send me a message describing a problem and I'll create a ticket!")
            return

        # Sort by most recent first
        my_issues.sort(key=lambda i: i.get("created_at", ""), reverse=True)

        lines = [f"📋 <b>Your Issues ({len(my_issues)})</b>", ""]
        for issue in my_issues[:15]:
            emoji = _PRIORITY_EMOJI.get(str(issue.get("priority", "")), "⚪")
            status = issue.get("status", "")
            loc = issue.get("location_display_name", "")
            if not loc:
                location = _location_by_id(issue.get("location_id", ""))
                loc = location["display_name"] if location else issue.get("location_id", "")
            lines.append(
                f"{emoji} <code>{issue['issue_id']}</code> | {status} | {_escape_html(loc)}\n"
                f"    {_escape_html(issue.get('description', '')[:80])}"
            )
            lines.append("")

        if len(my_issues) > 15:
            lines.append(f"<i>...and {len(my_issues) - 15} more</i>")

        lines.append("\nUse /status <code>&lt;ID&gt;</code> to see full details.")
        await self._send_message(chat_id, "\n".join(lines), parse_mode="HTML")

    async def _cmd_dashboard(self, chat_id: int) -> None:
        """Handle /dashboard — sustainability summary."""
        from tool import CATEGORIES, _issue_store, _location_by_id

        all_issues = _issue_store().all()
        total = len(all_issues)
        open_issues = [i for i in all_issues if str(i.get("status", "")).upper() not in ("RESOLVED", "CLOSED")]
        open_count = len(open_issues)
        resolved_count = total - open_count

        # Counts by category
        cat_counts: dict[str, int] = {cat: 0 for cat in CATEGORIES}
        for issue in open_issues:
            cat = issue.get("category", "")
            if cat in cat_counts:
                cat_counts[cat] += 1

        # Counts by priority
        priority_counts: dict[str, int] = {}
        for issue in open_issues:
            p = str(issue.get("priority", "")).upper()
            if p:
                priority_counts[p] = priority_counts.get(p, 0) + 1

        # Top locations with most open issues
        loc_counts: dict[str, int] = {}
        for issue in open_issues:
            lid = issue.get("location_id", "")
            if lid:
                loc_counts[lid] = loc_counts.get(lid, 0) + 1
        top_locations = sorted(loc_counts.items(), key=lambda kv: -kv[1])[:5]

        lines = [
            "📊 <b>CampusGreen Dashboard</b>",
            "",
            f"<b>Total Issues:</b> {total}",
            f"<b>Open:</b> {open_count}  |  <b>Resolved/Closed:</b> {resolved_count}",
            "",
            "<b>Open Issues by Category:</b>",
        ]
        for cat in CATEGORIES:
            count = cat_counts.get(cat, 0)
            if count > 0:
                bar = "█" * min(count, 10)
                lines.append(f"  {cat}: {bar} {count}")

        if priority_counts:
            lines.append("")
            lines.append("<b>Open Issues by Priority:</b>")
            for p in ["CRITICAL", "HIGH", "MEDIUM", "LOW"]:
                c = priority_counts.get(p, 0)
                if c > 0:
                    lines.append(f"  {_PRIORITY_EMOJI.get(p, '⚪')} {p}: {c}")

        if top_locations:
            lines.append("")
            lines.append("<b>Top Locations (open issues):</b>")
            for lid, count in top_locations:
                location = _location_by_id(lid)
                name = location["display_name"] if location else lid
                lines.append(f"  📍 {_escape_html(name)}: {count}")

        # Recent activity (last 5 issues)
        recent = sorted(all_issues, key=lambda i: i.get("created_at", ""), reverse=True)[:5]
        if recent:
            lines.append("")
            lines.append("<b>Recent Activity:</b>")
            for issue in recent:
                emoji = _PRIORITY_EMOJI.get(str(issue.get("priority", "")), "⚪")
                lines.append(
                    f"  {emoji} <code>{issue['issue_id']}</code> {issue.get('status', '')} — "
                    f"{_escape_html(issue.get('description', '')[:50])}"
                )

        await self._send_message(chat_id, "\n".join(lines), parse_mode="HTML")

    async def _cmd_categories(self, chat_id: int) -> None:
        """Handle /categories — show all issue categories."""
        lines = ["🏷️ <b>Issue Categories</b>", ""]
        for cat, desc in CATEGORY_DESCRIPTIONS.items():
            lines.append(f"<b>{cat}</b>")
            lines.append(f"  {desc}")
            lines.append("")
        lines.append("To report an issue, just describe it in a message and I'll categorize it for you!")
        await self._send_message(chat_id, "\n".join(lines), parse_mode="HTML")

    async def _cmd_tips(self, chat_id: int) -> None:
        """Handle /tips — send a random sustainability tip."""
        import secrets
        
        # Keep track of last sent tip to avoid repeats
        if not hasattr(self, "_last_tip"):
            self._last_tip = ""
            
        tip = secrets.choice(SUSTAINABILITY_TIPS)
        while tip == self._last_tip and len(SUSTAINABILITY_TIPS) > 1:
            tip = secrets.choice(SUSTAINABILITY_TIPS)
            
        self._last_tip = tip
        await self._send_message(chat_id, f"🌿 <b>Sustainability Tip</b>\n\n{tip}", parse_mode="HTML")

    async def _cmd_feedback(self, chat_id: int, args: str) -> None:
        """Handle /feedback <issue_id> <message>."""
        from tool import _issue_store, _validate_issue_id

        if not args:
            await self._send_message(
                chat_id,
                "⚠️ Please provide an issue ID and your feedback.\nExample: <code>/feedback WTR-001 Water is still leaking</code>",
                parse_mode="HTML",
            )
            return

        parts = args.split(None, 1)
        raw_id = parts[0]
        note_text = parts[1].strip() if len(parts) > 1 else ""

        issue_id = _validate_issue_id(raw_id)
        if issue_id is None:
            await self._send_message(
                chat_id,
                f"⚠️ <code>{_escape_html(raw_id)}</code> is not a valid issue ID.\nExpected format: <code>WTR-001</code>",
                parse_mode="HTML",
            )
            return

        if not note_text:
            await self._send_message(
                chat_id,
                f"⚠️ Please include a message after the issue ID.\nExample: <code>/feedback {issue_id} Water is still leaking</code>",
                parse_mode="HTML",
            )
            return

        store = _issue_store()
        issue = store.get(issue_id)
        if issue is None:
            await self._send_message(chat_id, f"❌ No issue found with ID <code>{issue_id}</code>.", parse_mode="HTML")
            return

        updated = store.update(issue_id, additional_note=f"[Telegram feedback] {note_text}")
        if updated is None:
            await self._send_message(chat_id, "❌ Failed to add feedback. Please try again.")
            return

        await self._send_message(
            chat_id,
            f"✅ Feedback added to <code>{issue_id}</code>\n\n"
            f"<b>Your note:</b> {_escape_html(note_text)}\n"
            f"<b>Issue status:</b> {updated.get('status', '')}",
            parse_mode="HTML",
        )

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
                    self._log.exception("Error during Telegram polling:")
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


