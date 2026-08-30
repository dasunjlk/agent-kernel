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
    search_issues,
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


def has_location_signal(prompt: str) -> bool:
    """True when the prompt appears to name a place, even an unknown one.

    Distinguishes "there's a leak" (no place, missing location) from "water leak
    near the old building" (a place was named but is not in the campus
    directory) so the driver asks for a location in the former and reports an
    unknown location in the latter, mirroring the agent's two behaviours.
    """
    lowered = prompt.lower()
    signals = (
        "near",
        "outside",
        "at the",
        "at a",
        "around",
        "by the",
        "beside",
        "in the",
        "on the",
        "behind",
        "next to",
        "building",
        "room",
        "lab",
        "block",
        "gate",
        "wing",
        "hall",
        "plaza",
        "walkway",
        "cafe",
        "library",
        "gym",
    )
    return any(signal in lowered for signal in signals)


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
        # session_id -> ordered list of issue ids touched in this conversation
        # (most recent last). Mirrors the LLM's recall of which issues were
        # discussed so follow-up references can be resolved by topic instead of
        # always defaulting to the single most recent issue. The issue records
        # themselves (get_issue) remain the source of truth.
        self.recent_issues: dict[str, list[str]] = {}
        # session_id -> {category, description} for a report awaiting its
        # location. Mirrors the LLM keeping an in-progress report in its working
        # memory so a follow-up that only supplies the missing location continues
        # the same task instead of starting over.
        self.pending: dict[str, dict] = {}
        # session_id -> {scope, top_category, ranking, counts, open_counts,
        # focused_category, issues} for the last action plan produced in this
        # conversation. Mirrors the LLM carrying the plan in the session's
        # context so follow-ups ("why is the first the highest priority?") can
        # explain the previous plan without the user repeating the request.
        self.last_plan: dict[str, dict[str, Any]] = {}
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

    def _note_issue(self, issue_id: str) -> None:
        """Record that ``issue_id`` was just created/fetched for this session.

        Keeps the conversation's recent-issue recall ordered by recency so a
        later reference can be resolved to the right ticket even when several
        issues were discussed.
        """
        if not self.current_session:
            return
        bucket = self.recent_issues.setdefault(self.current_session, [])
        if issue_id not in bucket:
            bucket.append(issue_id)
        else:
            bucket.remove(issue_id)
            bucket.append(issue_id)
        self.active[self.current_session] = issue_id

    def _issue_id_for_category(self, session_id: str, category: str) -> str | None:
        """Return the most recently discussed issue of ``category``, if any."""
        for issue_id in reversed(self.recent_issues.get(session_id) or []):
            result = get_issue(issue_id)
            if result["status"] == "ok" and result["issue"].get("category") == category:
                return issue_id
        return None

    def _resolve_reference(self, session_id: str, prompt: str) -> str | None:
        """Resolve a follow-up reference in ``prompt`` to a session issue.

        Prefers a topic match (for example "the leak" -> the WATER issue) over
        the single most recent issue, and falls back to the most recently
        discussed issue for generic references ("that issue", "the previous
        report"). Returns None when nothing can be resolved so the agent can
        ask for clarification instead of guessing.
        """
        lowered = prompt.lower()
        for category in ("WATER", "WASTE", "ENERGY", "FOOD", "POLLUTION", "INFRASTRUCTURE"):
            if detect_category(prompt) == category:
                matched = self._issue_id_for_category(session_id, category)
                if matched:
                    return matched
        if any(
            word in lowered
            for word in ("that issue", "the previous", "the report", "ticket i just", "the ticket", "the report")
        ):
            return self.recent_issues.get(session_id, [None])[-1]
        return self.active.get(session_id)

    def _status_summary(self, issue: dict[str, Any]) -> str:
        return (
            f"Ticket: {issue['issue_id']}\nStatus: {issue['status']}\nPriority: {issue['priority']}\n"
            f"Assigned team: {issue['assigned_team']}"
        )

    # --- Action planning (Phase 8) ---------------------------------------------

    def _recommended_action(self, category: str, count: int, open_issues: list[dict[str, Any]]) -> str:
        """Deterministic, operational recommendation grounded in the real records.

        Kept deliberately category-specific and operational (locations, tickets,
        assigned teams), never a generic slogan such as "use less water".
        """
        locations = sorted({item["location"] for item in open_issues})
        places = ", ".join(locations) if locations else "the reported locations"
        if category == "WATER":
            base = f"Inspect the water-leak locations ({places}) and prioritize maintenance on the reported leaks."
        elif category == "ENERGY":
            base = f"Review the reported energy-waste locations ({places}) and correct the lights/equipment at fault."
        elif category == "WASTE":
            base = f"Increase waste and recycling collection frequency at {places}."
        elif category == "FOOD":
            base = f"Review food preparation and portions at {places} to reduce avoidable food disposal."
        elif category == "POLLUTION":
            base = f"Inspect the source of the reported pollution at {places} and resolve it."
        elif category == "INFRASTRUCTURE":
            base = f"Schedule repairs for the damaged sustainability infrastructure at {places}."
        else:
            base = f"Review the reported issues at {places} and schedule the appropriate response."
        if count > 1:
            base += f" With {count} recorded report(s), this is worth a coordinated follow-up."
        return base

    def _open_issue_map(self) -> dict[str, Any]:
        """Return (status, open_issues_by_category, counts, top_locations) from real tools."""
        report = get_sustainability_report(period="month")
        if report["status"] != "ok":
            return {"ok": False}
        listing = search_issues(status="OPEN")
        if listing["status"] != "ok":
            return {"ok": False}
        grouped: dict[str, list[dict[str, Any]]] = {}
        for item in listing["issues"]:
            grouped.setdefault(item["category"], []).append(item)
        return {
            "ok": True,
            "counts": report["category_counts"],
            "top_locations": report["top_locations"],
            "open_by_category": grouped,
            "report": report,
        }

    def _general_plan(self) -> str:
        data = self._open_issue_map()
        if not data["ok"]:
            return "I could not build a plan right now because the sustainability data is unavailable."
        counts = data["counts"]
        open_by_category = data["open_by_category"]
        if not any(counts.values()):
            return "There are no recorded sustainability issues for this period yet, so there is nothing to prioritize."
        categories = [category for category in counts if counts[category] > 0]
        ranking = sorted(
            categories,
            key=lambda category: (-counts[category], -len(open_by_category.get(category, [])), category),
        )
        self.last_plan[self.current_session] = {
            "scope": "general",
            "top_category": ranking[0] if ranking else None,
            "ranking": list(ranking),
            "counts": dict(counts),
            "open_counts": {category: len(open_by_category.get(category, [])) for category in ranking},
        }
        lines = ["Here are the current sustainability priorities, based on the issues recorded this month:"]
        for index, category in enumerate(ranking[:3], start=1):
            open_issues = open_by_category.get(category, [])
            evidence = f"Evidence: {counts[category]} recorded issue(s) this month, " f"{len(open_issues)} still open."
            for item in open_issues[:3]:
                evidence += f" {item['issue_id']} ({item['priority']}, {item['status']}, {item['location']})."
            lines.append(f"\n{index}. {category}\n   {evidence}")
            lines.append(f"   Recommended action: {self._recommended_action(category, counts[category], open_issues)}")
        return "\n".join(lines)

    def _focused_plan(self, category: str) -> str:
        report = get_sustainability_report(period="month", category=category)
        listing = search_issues(category=category, status="OPEN")
        if report["status"] != "ok" or listing["status"] != "ok":
            return f"I could not build a plan for {category.title()} right now because the data is unavailable."
        count = report["category_counts"].get(category, 0)
        issues = listing["issues"]
        self.last_plan[self.current_session] = {
            "scope": "focused",
            "focused_category": category,
            "issues": [dict(item) for item in issues],
            "count": count,
        }
        if count == 0:
            return (
                f"There are no recorded {category.title()} issues for this period, so there is nothing to plan around."
            )
        lines = [
            f"Focused on {category.title()}: there {'is' if count == 1 else 'are'} {count} recorded "
            f"{category.title()} issue(s) this month, {'all still open.' if len(issues) == count else f'{len(issues)} still open.'}"
        ]
        for index, item in enumerate(issues[:3], start=1):
            action = self._recommended_action(category, count, issues)
            lines.append(
                f"\n{index}. {item['issue_id']} — {item['priority']}, {item['status']}, {item['location']}.\n"
                f"   Evidence: {item['description']}"
                f"\n   Recommended action: {action}"
            )
        return "\n".join(lines)

    def _location_plan(self) -> str:
        data = self._open_issue_map()
        if not data["ok"]:
            return "I could not build a location plan right now because the sustainability data is unavailable."
        counts = data["counts"]
        if not any(counts.values()):
            return "There are no recorded sustainability issues for this period, so I cannot rank any locations."
        top = data["top_locations"]
        by_id = {}
        for grouped in data["open_by_category"].values():
            for item in grouped:
                by_id.setdefault(item["location_id"], []).append(item)
        self.last_plan[self.current_session] = {
            "scope": "locations",
            "locations": [dict(item) for item in top],
        }
        if not top:
            return "The recorded issues do not associate with any known campus locations, so I cannot prioritize locations."
        lines = ["Based on this month's records, the campus locations with the most sustainability activity are:"]
        for index, location in enumerate(top[:5], start=1):
            location_issues = by_id.get(location["location_id"], [])
            detail = ", ".join(
                f"{item['issue_id']} ({item['priority']}, {item['status']})" for item in location_issues[:3]
            )
            lines.append(
                f"\n{index}. {location['display_name']} — {location['count']} recorded issue(s)."
                f"{(' ' + detail + '.') if detail else ''}"
            )
            lines.append(
                f"   Recommended action: {self._recommended_action('OTHER', location['count'], location_issues)}"
            )
        return "\n".join(lines)

    def _escalate_action(self, prompt: str) -> str | None:
        """Act on an explicit escalation request; returns the reply or None if not an escalation."""
        lowered = prompt.lower()
        if "escalate" not in lowered:
            return None
        issue = None
        id_match = re.search(r"\b([A-Z]{3}-\d{3,})\b", prompt.upper())
        if id_match:
            result = get_issue(id_match.group(1))
            if result["status"] == "ok":
                issue = result["issue"]
            else:
                return f"I could not find issue {id_match.group(1)} to escalate. Could you double-check the ticket ID?"
        else:
            category = detect_category(prompt)
            if category is None:
                return (
                    "Which issue would you like me to escalate? Give me the ticket ID, "
                    "or tell me the type of problem (for example the water or energy issue)."
                )
            listing = search_issues(category=category, status="OPEN")
            if listing["status"] != "ok":
                return "I could not look up the open issues right now."
            open_issues = listing["issues"]
            if not open_issues:
                return (
                    "I could not find an open issue to escalate"
                    + (f" for {category}." if category else ".")
                    + " Could you give me the ticket ID?"
                )
            issue = open_issues[0]
        updated = update_issue(
            issue["issue_id"],
            priority="CRITICAL",
            status="ESCALATED",
            additional_note="Escalated on the user's explicit request.",
        )
        if updated["status"] != "ok":
            return f"I could not escalate {issue['issue_id']} right now."
        self._note_issue(issue["issue_id"])
        notified = notify_team(
            team_id=updated["issue"]["assigned_team_id"],
            issue_id=updated["issue"]["issue_id"],
            notification_type="escalation",
            message=f"Escalated to Critical on the user's request: ticket {updated['issue']['issue_id']}.",
        )
        if notified["status"] == "ok":
            return (
                f"Escalated {updated['issue']['issue_id']} to Critical ({issue['status']} -> ESCALATED). "
                f"The {updated['issue']['assigned_team']} team has been notified."
            )
        return (
            f"Escalated {updated['issue']['issue_id']} to Critical, but the team notification failed. "
            f"The {updated['issue']['assigned_team']} team could not be notified right now."
        )

    def _plan_intent(self, lowered: str) -> bool:
        return any(
            word in lowered
            for word in (
                "prioriti",
                "prioritize",
                "recommend",
                "action plan",
                "do about",
                "should we",
                "what should i",
                "how should we",
                "which campus",
            )
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

        # --- Action planning (Phase 8) -----------------------------------------
        # Ordered before analytics and reporting so planning prompts that also
        # mention "month" or a category resolve as plans, not analytics or new
        # reports. Analysis branches never perform operational actions; only the
        # explicit escalation branch acts (update + notify) and only when the
        # user asked for it.
        money_intent = "how much" in lowered and any(
            w in lowered for w in ("money", "save", "saving", "cost", "dollar", "budget")
        )
        cost_intent = any(
            w in lowered
            for w in ("save by", "will it cost", "how much will", "cost us", "university save", "the university save")
        )
        if money_intent or cost_intent:
            return (
                "I can't calculate costs or savings from the available data. I can tell you which issues "
                "were actually recorded and recommend priorities, but I don't have a cost or savings model."
            )

        escalated = self._escalate_action(prompt)
        if escalated is not None:
            return escalated

        if "why" in lowered:
            plan = self.last_plan.get(self.current_session) or {}
            top = plan.get("top_category")
            if top:
                counts = plan["counts"]
                open_count = plan.get("open_counts", {}).get(top, 0)
                return (
                    f"{top} is the top priority because it has the most recorded issues this period "
                    f"({counts[top]} report(s)), and {open_count} of them are still open."
                )
            focused = plan.get("focused_category")
            if focused:
                return (
                    f"Within {focused.title()}, the plan follows the recorded tickets: higher-priority "
                    "open issues come first, and every recommended action stays grounded in what was actually reported."
                )
            return "Could you tell me which part of the plan you'd like me to explain?"

        if self._plan_intent(lowered):
            if any(w in lowered for w in ("location", "locations", "where on campus", "which areas", "campus should")):
                return self._location_plan()
            category = detect_category(prompt)
            if category:
                return self._focused_plan(category)
            return self._general_plan()

        # --- Status / ticket lookup by explicit ID --------------------------
        id_status_intent = any(
            word in lowered
            for word in (
                "status",
                "ticket",
                "check",
                "how is",
                "update",
                "happening",
                "resolved",
                "going on",
                "current",
                "about the",
            )
        )
        id_match = re.search(r"\b([A-Z]{3}-\d{3,})\b", prompt.upper())
        if id_match and id_status_intent:
            result = get_issue(id_match.group(1))
            if result["status"] == "ok":
                self._note_issue(result["issue"]["issue_id"])
                return self._status_summary(result["issue"])
            return f"I could not find issue {id_match.group(1)}. If you have a ticket ID please double-check it, or describe what you reported and where."

        # --- Status request without an ID (resolves topic references, else active)
        # A narrower intent set: without an explicit ID, words like "ticket",
        # "update", "current", or "about the" alone are not a standalone status
        # question (e.g. "and create the ticket immediately"), so they are not
        # treated as a status lookup here.
        status_intent = any(
            word in lowered for word in ("status", "check", "how is", "happening", "resolved", "going on")
        )
        if status_intent:
            target = self._resolve_reference(self.current_session, prompt)
            if target:
                result = get_issue(target)
                if result["status"] == "ok":
                    self._note_issue(result["issue"]["issue_id"])
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
            self._note_issue(issue_id)
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
            active = self._resolve_reference(self.current_session, prompt) or self.active.get(self.current_session)
            if not active:
                return "I could not tell which issue you mean. Could you give me the ticket ID?"
            current = get_issue(active)
            if current["status"] != "ok":
                return f"I could not retrieve ticket {active}. Please try again in a moment."
            updated = update_issue(active, priority="CRITICAL", status="ESCALATED", additional_note=prompt)
            if updated["status"] == "ok":
                self._note_issue(active)
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
            active = self._resolve_reference(self.current_session, prompt) or self.active.get(self.current_session)
            if active:
                records = [n for n in campus_tool._issue_store().notifications if n.get("issue_id") == active]
                if records:
                    self._note_issue(active)
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

        # Continuation: the user just supplied the location a pending report was
        # waiting on (for example "there's a leak." -> "near Lab 3."). Complete the
        # same report instead of treating it as a new request.
        if category is None and location_display is not None and self.current_session in self.pending:
            pending = self.pending.pop(self.current_session)
            lookup = lookup_campus_location(location_display)
            priority = decide_priority(pending["category"], pending["description"])
            created = create_issue(
                category=pending["category"],
                description=pending["description"],
                location_id=lookup["location"]["location_id"],
                priority=priority,
            )
            if created["status"] != "ok":
                return "I could not create the maintenance request right now. Please try again."
            issue_id = created["issue_id"]
            team_name = created["issue"]["assigned_team"]
            notify_team(team_id=created["assigned_team_id"], issue_id=issue_id, notification_type="new_issue")
            self._note_issue(issue_id)
            return (
                f"{pending['category'].title()} issue reported.\nTicket: {issue_id}\nLocation: {location_display}\n"
                f"Priority: {priority}\nAssigned team: {team_name}\nStatus: Reported\n"
                f"The {team_name} team has been notified."
            )

        if category is not None and location_display is None:
            if has_location_signal(prompt):
                lookup_campus_location(prompt)  # mirrors the real flow (result: not found)
                return "I couldn't identify that campus location. Could you provide the building name, room number, or a nearby known landmark?"
            self.pending[self.current_session] = {"category": category, "description": prompt}
            return "I can help with that. Which campus building, room, or landmark is it near?"
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
            self._note_issue(issue_id)
            return (
                f"{category.title()} issue reported.\nTicket: {issue_id}\nLocation: {location_display}\n"
                f"Priority: {priority}\nAssigned team: {team_name}\nStatus: Reported\n"
                f"The {team_name} team has been notified."
            )

        # --- Unsupported / out-of-scope request ------------------------------------
        if any(
            word in lowered
            for word in ("book", "reserve a", "book a", "schedule a class", "order food", "pay", "enroll", "bus")
        ):
            return (
                "That's outside what I can do. I'm CampusGreen, your campus sustainability coordinator: "
                "I can report and track sustainability issues, check a ticket's status, coordinate team "
                "notifications, and summarize campus sustainability trends. Is there a sustainability "
                "issue you'd like to report?"
            )

        # --- Recovery after a failed action -----------------------------------------
        if "try again" in lowered or "retry" in lowered:
            active = self.active.get(self.current_session)
            if active:
                # An issue was already created; do not duplicate it. Confirm its state.
                result = get_issue(active)
                if result["status"] == "ok":
                    self._note_issue(active)
                    return (
                        f"Ticket {active} already exists on record. "
                        "Let me know if you'd like me to update or escalate it."
                    )
            return (
                "I can retry that for you. Could you describe the issue and where it is, "
                "or give me the ticket ID if you're following up on one?"
            )

        # --- Brief conversational courtesies (no tool) ------------------------------
        stripped = prompt.strip().lower().rstrip("!")
        if stripped in {
            "hello",
            "hi",
            "hey",
            "thanks",
            "thank you",
            "okay",
            "ok",
            "got it",
            "that's all",
            "great",
            "good",
            "cool",
            "bye",
        }:
            return "You're welcome! I'm here if you need to report an issue or check on one."

        # --- Fallback ----------------------------------------------------------------
        return (
            "I coordinate campus sustainability issues such as water, energy, waste, and pollution. "
            "Could you describe what you're seeing and where it is?"
        )
