"""Shared offline helpers for the CampusGreen reliability test suite.

These reuse the pattern established in ``integration_test.py``: the WhatsApp
handler is constructed without running its real ``__init__`` (no Meta
credentials needed), outbound sends are recorded in memory, and the
``AgentService`` the handler instantiates is swapped for a scripted stand-in
that drives the **real** CampusGreen tools (``tool.py``). Everything runs
against an isolated copy of the seed data and never touches a live messaging
service or an LLM.

``CampusGreenDriver`` is a deterministic stand-in for the LLM. It mirrors the
workflow rules encoded in the CampusGreen system instructions (report ->
lookup -> create -> notify, escalate on worsening, resolve follow-ups to the
session's active issue, answer status/analytics from real tool results, and
report tool failures truthfully). It is a *fake for the reasoning layer*, not
a measure of LLM quality: it lets the automated suite verify that the
application path (routing, session isolation, tools, persistence, truthful
responses) works end to end without a network or API key.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from agentkernel.core.model import AgentReplyText

import tool as campus_tool
from tool import (
    create_issue,
    get_issue,
    get_sustainability_report,
    lookup_campus_location,
    notify_team,
    update_issue,
)

DATA_FILES = ["locations.json", "teams.json", "issues.json", "sustainability.json"]

FROM_A = "15550000001"
FROM_B = "15550000002"

CATEGORY_TRIGGERS: dict[str, tuple[str, ...]] = {
    "WATER": ("water", "leak", "tap", "dripping", "flood", "drain"),
    "ENERGY": ("light", "electric", "air conditioning", "fan", "power", "energy"),
    "WASTE": ("bin", "garbage", "trash", "litter", "recycl", "overflow"),
    "FOOD": ("food", "cafeteria", "meal", "kitchen"),
    "POLLUTION": ("smoke", "chemical", "fume", "pollution", "smell"),
    "INFRASTRUCTURE": ("broken", "cracked", "damaged", "solar", "mounting", "tile"),
}


def seed_data_dir(target: Path) -> None:
    """Copy the committed seed data files into ``target``."""
    source = Path(__file__).resolve().parent / "data"
    target.mkdir(parents=True, exist_ok=True)
    for name in DATA_FILES:
        shutil.copyfile(source / name, target / name)


@contextmanager
def isolated_data_env():
    """Create a seeded temp data dir and point CAMPUSGREEN_DATA_DIR at it."""
    target = Path(tempfile.mkdtemp(prefix="campusgreen-helper-data-"))
    seed_data_dir(target)
    old = os.environ.get("CAMPUSGREEN_DATA_DIR")
    os.environ["CAMPUSGREEN_DATA_DIR"] = str(target)
    try:
        yield target
    finally:
        if old is None:
            os.environ.pop("CAMPUSGREEN_DATA_DIR", None)
        else:
            os.environ["CAMPUSGREEN_DATA_DIR"] = old


def new_handler():
    """Construct the WhatsApp handler without running __init__ (no credentials)."""
    from agentkernel.integration.whatsapp.whatsapp_chat import AgentWhatsAppRequestHandler

    handler = object.__new__(AgentWhatsAppRequestHandler)
    handler._log = logging.getLogger("ak.api.whatsapp.campusgreen.test")
    handler._whatsapp_agent = "campusgreen"
    handler._whatsapp_agent_acknowledgement = None
    handler._max_file_size = 10 * 1024 * 1024
    handler._app_secret = None
    handler.sent = []
    handler.last_sender = None

    async def fake_send(to_number, text, reply_to_message_id=None):
        handler.last_sender = to_number
        handler.sent.append((to_number, text, reply_to_message_id))

    handler._send_message = fake_send
    return handler


def new_shim_handler():
    """Construct the CampusGreen WhatsApp shim without running __init__.

    Identical to ``new_handler()`` but builds ``CampusGreenWhatsAppHandler``
    (the thin normalization + duplicate-event boundary — see
    ``whatsapp_handler.py``) so the Phase 6 shim's own behavior is testable
    offline with a scripted ``AgentService`` and an in-memory send recorder.
    """
    from whatsapp_handler import CampusGreenWhatsAppHandler

    handler = new_handler()
    shim = object.__new__(CampusGreenWhatsAppHandler)
    for attr in ("_log", "_whatsapp_agent", "_whatsapp_agent_acknowledgement", "_max_file_size", "_app_secret"):
        setattr(shim, attr, getattr(handler, attr))
    shim.sent = []
    shim.last_sender = None

    async def fake_send(to_number, text, reply_to_message_id=None):
        shim.last_sender = to_number
        shim.sent.append((to_number, text, reply_to_message_id))

    shim._send_message = fake_send
    shim._seen_message_ids = set()
    return shim


def install_service(service, monkeypatch):
    """Swap the handler's AgentService factory so it yields the given fake."""
    import agentkernel.integration.whatsapp.whatsapp_chat as whatsapp_chat

    monkeypatch.setattr(whatsapp_chat, "AgentService", lambda: service)


def text_message(body="hello", from_number=FROM_A, message_id="m1"):
    return {"id": message_id, "from": from_number, "type": "text", "text": {"body": body}}


def reload_issues(path: Path) -> tuple[list, list]:
    import json

    raw = json.loads(path.read_text(encoding="utf-8"))
    return raw["issues"], raw["notifications"]


def detect_category(prompt: str) -> str | None:
    lowered = prompt.lower()
    for category, triggers in CATEGORY_TRIGGERS.items():
        if any(trigger in lowered for trigger in triggers):
            return category
    return None


def known_location_in(prompt: str) -> str | None:
    """Return the display name of the first known location mentioned in prompt."""
    lowered = prompt.lower()
    for location in campus_tool._load_json("locations.json"):
        candidates = [str(location.get("display_name", ""))]
        candidates.extend(str(alias) for alias in location.get("aliases") or [])
        for candidate in candidates:
            value = candidate.strip().lower()
            if value and value in lowered:
                return location["display_name"]
    return None


def decide_priority(category: str, prompt: str) -> str:
    """Deterministic priority assessment mirroring SPEC section 12 examples."""
    lowered = prompt.lower()
    if category == "WATER":
        return "MEDIUM" if "dripping" in lowered else "HIGH"
    if category == "ENERGY":
        return "HIGH" if any(word in lowered for word in ("wing", "corridor", "entire", "whole")) else "MEDIUM"
    if category == "WASTE":
        return "HIGH" if any(word in lowered for word in ("blocking", "walkway", "hygiene")) else "MEDIUM"
    if category == "POLLUTION":
        return "HIGH" if any(word in lowered for word in ("smoke", "burning")) else "MEDIUM"
    return "MEDIUM"


class FakeAgentService:
    """Stands in for AgentService; records select()/run_multi() without real agents."""

    def __init__(self, agent_truthy=True, reply=None, error=None):
        self._agent = agent_truthy
        self.reply = reply if reply is not None else AgentReplyText(response="agent says hi")
        self.error = error
        self.selects = []
        self.run_calls = []

    @property
    def agent(self):
        return self._agent

    def select(self, session_id=None, name=None):
        self.selects.append((session_id, name))

    async def run_multi(self, requests):
        if self.error:
            raise self.error
        self.run_calls.append(requests)
        return self.reply


class CampusGreenDriver(FakeAgentService):
    """Deterministic stand-in for the CampusGreen agent driving the REAL tools."""

    def __init__(self) -> None:
        super().__init__(agent_truthy=True)
        # session_id -> most recently touched issue id (mirrors session memory)
        self.active: dict[str, str] = {}
        self.current_session: str | None = None

    def select(self, session_id=None, name=None):
        super().select(session_id, name)
        self.current_session = session_id

    async def run_multi(self, requests):
        self.run_calls.append(requests)
        first = requests[0]
        prompt = getattr(first, "prompt", "")
        return AgentReplyText(response=self._agent_result(prompt))

    def _reply(self, text: str) -> str:
        return text

    def _status_summary(self, issue: dict[str, Any]) -> str:
        return (
            f"Ticket: {issue['issue_id']}\nStatus: {issue['status']}\nPriority: {issue['priority']}\n"
            f"Assigned team: {issue['assigned_team']}"
        )

    def _agent_result(self, prompt: str) -> str:
        lowered = prompt.lower()

        # --- Prompt-injection / role-override attempts ---------------------
        if "ignore" in lowered or "disregard" in lowered:
            return "I can't change how I work. I only create tickets for verified campus locations through my tools."
        if "pretend" in lowered:
            return "I won't claim an action that didn't happen. I only report what my tools confirm."
        if "director" in lowered or "personally" in lowered:
            return "I notify the responsible campus team through my notification tool; I don't contact or name individuals directly."
        if "how much" in lowered and any(
            w in lowered for w in ("electricity", "consume", "consumption", "kwh", "meter")
        ):
            return "I don't have live meter data. I can summarize the campus issues that were actually recorded, but I can't estimate consumption or costs."
        if "old science" in lowered or "lab 99" in lowered:
            return "I could not find that building in the campus directory. Could you name a building, room, or landmark I know?"

        # --- Status / ticket lookup by explicit ID --------------------------
        id_match = re.search(r"\b([A-Z]{3}-\d{3,})\b", prompt.upper())
        if id_match and any(word in lowered for word in ("status", "ticket", "check", "how is", "update")):
            result = get_issue(id_match.group(1))
            if result["status"] == "ok":
                self.active[self.current_session] = result["issue"]["issue_id"]
                return self._status_summary(result["issue"])
            return f"I could not find issue {id_match.group(1)}. If you have a ticket ID please double-check it, or describe what you reported and where."

        # --- Status request without an ID (resolves to session's issue) ------
        if "status" in lowered:
            active = self.active.get(self.current_session)
            if active:
                result = get_issue(active)
                if result["status"] == "ok":
                    return self._status_summary(result["issue"])
            return "I need to know which issue you mean. Could you give me the ticket ID?"

        # --- Analytics --------------------------------------------------------
        if any(word in lowered for word in ("sustainability", "biggest", "how many", "summary", "trends", "month")):
            period = "quarter" if "quarter" in lowered else ("week" if "week" in lowered else "month")
            report = get_sustainability_report(period=period)
            if report["status"] == "ok":
                counts = report["category_counts"]
                top = max(counts, key=lambda category: counts[category]) if any(counts.values()) else None
                if top is None:
                    return "There are no recorded issues for that period yet."
                return f"{top} is the most common recorded issue this period with {counts[top]} report(s)."
            return "I could not produce the sustainability report right now."

        # --- Forced failure probes (test-controlled, exercised through real tools)
        # Ordered before the notify/escalation branches so "force notify failure"
        # and friends are always treated as probes and never as real follow-ups.
        if "force notify failure" in lowered:
            lookup = lookup_campus_location("Lab 3")
            created = create_issue(
                category="WATER", description=prompt, location_id=lookup["location"]["location_id"], priority="HIGH"
            )
            issue_id = created["issue_id"]
            self.active[self.current_session] = issue_id
            result = notify_team(team_id="team_does_not_exist", issue_id=issue_id, notification_type="new_issue")
            assert result["status"] == "error"
            return f"Ticket {issue_id} was created, but I could not notify the responsible team right now."
        if "force lookup failure" in lowered:
            result = lookup_campus_location("near the old building")
            assert result["status"] == "error"
            return "I couldn't identify that campus location. Could you provide the building name, room number, or a nearby known landmark?"
        if "force get failure" in lowered:
            result = get_issue("WTR-999")
            assert result["status"] == "error"
            return "I could not find that issue. If you have a ticket ID please double-check it."
        if "force update failure" in lowered:
            active = self.active.get(self.current_session) or "WTR-001"
            result = update_issue(active, status="NOT_A_STATUS")
            assert result["status"] == "error"
            return "I could not update the ticket right now."
        if "force create failure" in lowered or "force failure" in lowered:
            result = create_issue(
                category="WATER", description="Water leak", location_id="loc_not_real", priority="HIGH"
            )
            assert result["status"] == "error"
            return "I could not create the maintenance request right now. Please try again."

        # --- Follow-up escalation -----------------------------------------------
        if any(word in lowered for word in ("getting worse", "worse", "spreading", "getting bigger")):
            active = self.active.get(self.current_session)
            if not active:
                return "I could not tell which issue you mean. Could you give me the ticket ID?"
            current = get_issue(active)
            if current["status"] != "ok":
                return f"I could not retrieve ticket {active}. Please try again in a moment."
            updated = update_issue(active, priority="CRITICAL", status="ESCALATED", additional_note=prompt)
            if updated["status"] == "ok":
                team_id = updated["issue"]["assigned_team_id"]
                notify_team(
                    team_id=team_id,
                    issue_id=active,
                    notification_type="escalation",
                    message=f"Escalated: {prompt} (ticket {active}).",
                )
                return f"Ticket {active} has been escalated to Critical. The report was updated and the responsible team has been notified."
            return "I could not update the ticket right now."

        # --- "Has the team been notified?" (answer from recorded state) --------
        if "notified" in lowered or "notify" in lowered:
            active = self.active.get(self.current_session)
            if active:
                records = [n for n in campus_tool._issue_store().notifications if n.get("issue_id") == active]
                if records:
                    return f"Yes — {records[-1].get('team_name', 'the responsible team')} was notified for ticket {active}."
                return f"No notification has been recorded for ticket {active} yet."
            return "I'd need to know which issue you mean. Could you give me the ticket ID?"

        # --- Capabilities / general questions (no tools) ------------------------
        if any(word in lowered for word in ("what can you", "what do you do", "can you help", "how do you work")):
            return (
                "I coordinate campus sustainability issues: water, energy, waste, food, pollution, and infrastructure. "
                "I can report an issue, check a ticket's status, update or escalate a report, and summarize campus trends from recorded issues."
            )

        # --- Missing information -------------------------------------------------
        if "problem" in lowered and detect_category(prompt) is None:
            return "I can help report it. What type of problem are you seeing, and where is it located?"

        # --- New issue report ------------------------------------------------------
        category = detect_category(prompt)
        location_display = known_location_in(prompt)
        if category is not None and location_display is None:
            lookup_campus_location(prompt)  # mirrors the real flow (result: not found)
            return "I couldn't identify that campus location. Could you provide the building name, room number, or a nearby known landmark?"
        if category is not None and location_display is not None:
            lookup = lookup_campus_location(location_display)
            priority = decide_priority(category, prompt)
            created = create_issue(
                category=category,
                description=prompt,
                location_id=lookup["location"]["location_id"],
                priority=priority,
            )
            if created["status"] != "ok":
                return "I could not create the maintenance request right now. Please try again."
            issue_id = created["issue_id"]
            team_name = created["issue"]["assigned_team"]
            notify_team(team_id=created["assigned_team_id"], issue_id=issue_id, notification_type="new_issue")
            self.active[self.current_session] = issue_id
            return (
                f"{category.title()} issue reported.\nTicket: {issue_id}\nLocation: {location_display}\n"
                f"Priority: {priority}\nAssigned team: {team_name}\nStatus: Reported\n"
                f"The {team_name} team has been notified."
            )

        # --- Fallback ----------------------------------------------------------------
        return (
            "I coordinate campus sustainability issues such as water, energy, waste, and pollution. "
            "Could you describe what you're seeing and where it is?"
        )
