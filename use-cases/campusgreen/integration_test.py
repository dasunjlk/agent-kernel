"""Tests for the CampusGreen WhatsApp integration layer.

These verify the Agent Kernel messaging boundary (``AgentWhatsAppRequestHandler``)
exactly as the installed Agent Kernel builds it: the handler is constructed
without running its ``__init__`` (so no Meta credentials are needed), its outbound
``_send_message`` is replaced with an in-memory recorder, and the ``AgentService``
the handler instantiates is swapped for a scripted stand-in so no real agent or
live network is touched.

Two layers are covered:

- **Routing / session / errors** (``FakeAgentService``): an incoming WhatsApp text
  message reaches the agent service with the right prompt, ``session_id``
  (= sender number) and agent name; distinct senders get distinct sessions; the
  agent reply is sent back as a WhatsApp message; a missing agent and a raised
  error both map to friendly WhatsApp messages.
- **Tool workflows through the handler** (``CampusgreenAgentService``): a
  deterministic stand-in for the LLM agent drives the **real** CampusGreen tools
  (``tool.py``) for every required scenario — report (lookup -> create -> notify),
  unknown location (no issue created), status request (get_issue), escalation
  (update + notify), and truthful tool-failure reporting.

All tests run against an isolated copy of the seed data via ``CAMPUSGREEN_DATA_DIR``
and never call a live messaging service or the OpenAI API.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile
from pathlib import Path

import pytest
from agentkernel.core.model import AgentReplyText

import tool as campus_tool
from tool import (
    create_issue,
    get_issue,
    lookup_campus_location,
    notify_team,
    update_issue,
)

DATA_FILES = ["locations.json", "teams.json", "issues.json", "sustainability.json"]

FROM_A = "15550000001"
FROM_B = "15550000002"


def _seed_data_dir(target: Path) -> None:
    source = Path(__file__).resolve().parent / "data"
    target.mkdir(parents=True, exist_ok=True)
    for name in DATA_FILES:
        shutil.copyfile(source / name, target / name)


def _new_handler():
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


def _install_service(service, monkeypatch):
    """Swap the handler's AgentService factory so it yields the given fake."""
    import agentkernel.integration.whatsapp.whatsapp_chat as whatsapp_chat

    monkeypatch.setattr(whatsapp_chat, "AgentService", lambda: service)


def _text_message(body="hello", from_number=FROM_A, message_id="m1"):
    return {"id": message_id, "from": from_number, "type": "text", "text": {"body": body}}


def _reload_issues(path: Path) -> tuple[list, list]:
    import json

    raw = json.loads(path.read_text(encoding="utf-8"))
    return raw["issues"], raw["notifications"]


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


class CampusgreenAgentService(FakeAgentService):
    """Deterministic stand-in for the CampusGreen agent driving the REAL tools.

    ``select`` records the sender's session; ``run_multi`` inspects the text prompt
    and calls the actual CampusGreen tools in the order the agent is instructed to,
    building a reply from the real tool results, so the responses and persisted
    state reflect genuine tool work. Per-session context is tracked so follow-ups
    (status / escalation) know which ticket the sender created.
    """

    def __init__(self) -> None:
        super().__init__(agent_truthy=True)
        # session_id -> most recently touched issue id
        self.active: dict[str, str] = {}
        self.current_session: str | None = None

    def select(self, session_id=None, name=None):
        super().select(session_id, name)
        self.current_session = session_id

    def _agent_result(self, prompt: str) -> AgentReplyText:
        lowered = prompt.lower()

        if "old building" in lowered and "leak" in lowered:
            lookup = lookup_campus_location("near the old building")
            if lookup["status"] == "error":
                return AgentReplyText(
                    response="I couldn't identify that campus location. Could you provide the building name, room number, or a nearby known landmark?"
                )
            return AgentReplyText(response="unexpected: location resolved")

        if "force failure" in lowered:
            failed = create_issue(
                category="WATER",
                description="Water leak",
                location_id="loc_not_real",
                priority="HIGH",
            )
            assert failed["status"] == "error"
            return AgentReplyText(response="I could not create the maintenance request right now. Please try again.")

        if "status of" in lowered:
            result = get_issue("WTR-001")
            if result["status"] == "ok":
                issue = result["issue"]
                return AgentReplyText(
                    response=(
                        f"Ticket: {issue['issue_id']}\n"
                        f"Status: {issue['status']}\n"
                        f"Priority: {issue['priority']}\n"
                        f"Assigned team: {issue['assigned_team']}"
                    )
                )
            return AgentReplyText(response="I could not find that issue.")

        if "worse" in lowered or "spreading" in lowered:
            active_issue_id = self.active.get(self.current_session)
            if not active_issue_id:
                return AgentReplyText(
                    response="I could not tell which issue you mean. Could you give me the ticket ID?"
                )
            get_issue(active_issue_id)
            updated = update_issue(
                active_issue_id,
                priority="CRITICAL",
                status="ESCALATED",
                additional_note="Leak is spreading across the floor.",
            )
            if updated["status"] == "ok":
                notify_team(
                    team_id=updated["issue"]["assigned_team_id"],
                    issue_id=active_issue_id,
                    message=f"Escalated: leak is spreading across the floor (ticket {active_issue_id}).",
                    notification_type="escalation",
                )
                return AgentReplyText(
                    response=f"Ticket {active_issue_id} has been escalated to Critical. The responsible team has been notified."
                )
            return AgentReplyText(response="I could not update the ticket right now.")

        if "sustainability" in lowered or "biggest" in lowered:
            from tool import get_sustainability_report

            report = get_sustainability_report(period="month")
            counts = report["category_counts"]
            return AgentReplyText(
                response=f"Energy leads the recorded issues this month with {counts['ENERGY']} report(s)."
            )

        # Default: report a water leak at Lab 3 (lookup -> create -> notify)
        lookup = lookup_campus_location("Lab 3")
        created = create_issue(
            category="WATER",
            description=prompt,
            location_id=lookup["location"]["location_id"],
            priority="HIGH",
        )
        issue_id = created["issue_id"]
        notify_team(team_id=lookup["location"]["responsible_team_id"], issue_id=issue_id, notification_type="new_issue")
        self.active[self.current_session] = issue_id
        return AgentReplyText(response=f"Water leak reported. Ticket: {issue_id}")

    async def run_multi(self, requests):
        self.run_calls.append(requests)
        first = requests[0]
        prompt = getattr(first, "prompt", "")
        return self._agent_result(prompt)


@pytest.fixture(scope="session")
def isolated_data_dir() -> Path:
    target = Path(tempfile.mkdtemp(prefix="campusgreen-wa-data-"))
    _seed_data_dir(target)
    os.environ["CAMPUSGREEN_DATA_DIR"] = str(target)
    yield target
    os.environ.pop("CAMPUSGREEN_DATA_DIR", None)
    os.environ.pop("CAMPUSGREEN_CHANNEL", None)


@pytest.fixture
def isolated_store(isolated_data_dir, monkeypatch):
    """Point the module-global issue store at the isolated data dir for this test."""
    store = campus_tool.IssueStore()
    monkeypatch.setattr(campus_tool, "_ISSUE_STORE", store)
    return store


# --- Routing / session isolation / errors -------------------------------------


@pytest.mark.asyncio
async def test_message_routes_text_to_agent_service(monkeypatch):
    service = FakeAgentService()
    _install_service(service, monkeypatch)
    handler = _new_handler()

    await handler._handle_message(_text_message("There's a water leak outside Lab 3."), {})

    assert len(service.selects) == 1
    session_id, name = service.selects[0]
    assert session_id == FROM_A
    assert name == "campusgreen"

    assert len(service.run_calls) == 1
    requests = service.run_calls[0]
    assert requests[0].prompt == "There's a water leak outside Lab 3."


@pytest.mark.asyncio
async def test_session_isolation_between_senders(monkeypatch):
    service = FakeAgentService()
    _install_service(service, monkeypatch)
    handler = _new_handler()

    await handler._handle_message(_text_message("hello A", from_number=FROM_A), {})
    await handler._handle_message(_text_message("hello B", from_number=FROM_B), {})

    assert [s for s, _ in service.selects] == [FROM_A, FROM_B]
    assert service.selects[0][0] != service.selects[1][0]


@pytest.mark.asyncio
async def test_agent_reply_is_sent_back_as_whatsapp_message(monkeypatch):
    service = FakeAgentService(reply=AgentReplyText(response="Ticket: WTR-001"))
    _install_service(service, monkeypatch)
    handler = _new_handler()

    await handler._handle_message(_text_message("There's a water leak outside Lab 3."), {})

    assert handler.sent
    to_number, text, reply_to = handler.sent[-1]
    assert to_number == FROM_A
    assert text == "Ticket: WTR-001"
    assert reply_to == "m1"


@pytest.mark.asyncio
async def test_missing_agent_maps_to_friendly_message(monkeypatch):
    service = FakeAgentService(agent_truthy=False)
    _install_service(service, monkeypatch)
    handler = _new_handler()

    await handler._handle_message(_text_message(), {})

    assert handler.sent[-1][1] == "Sorry, no agent is available to handle your request."
    assert service.run_calls == [], "agent must not run when none was selected"


@pytest.mark.asyncio
async def test_generic_error_maps_to_friendly_message(monkeypatch):
    service = FakeAgentService(error=RuntimeError("agent blew up"))
    _install_service(service, monkeypatch)
    handler = _new_handler()

    await handler._handle_message(_text_message(), {})

    assert handler.sent[-1][1] == "Sorry, there was an error processing your request."


@pytest.mark.asyncio
async def test_audio_message_rejected_before_agent(monkeypatch):
    service = FakeAgentService()
    _install_service(service, monkeypatch)
    handler = _new_handler()

    await handler._handle_message({"id": "m3", "from": FROM_A, "type": "audio"}, {})

    assert service.selects == []
    assert service.run_calls == []
    assert handler.sent[-1][1] == "Sorry, audio and video messages are not supported yet."


# --- Tool workflows through the handler (real tools) ---------------------------


@pytest.mark.asyncio
async def test_report_workflow_lookup_create_notify(isolated_store, isolated_data_dir, monkeypatch):
    service = CampusgreenAgentService()
    _install_service(service, monkeypatch)
    handler = _new_handler()
    before, _ = _reload_issues(Path(isolated_data_dir) / "issues.json")
    seed_ids = {item["issue_id"] for item in before}

    await handler._handle_message(_text_message("There's a water leak outside Lab 3."), {})

    issues, notifications = _reload_issues(Path(isolated_data_dir) / "issues.json")
    new_ids = [item["issue_id"] for item in issues if item["issue_id"] not in seed_ids]
    assert len(new_ids) == 1
    ticket = new_ids[0]
    assert re.fullmatch(r"[A-Z]{3}-\d{3,}", ticket)

    created = next(item for item in issues if item["issue_id"] == ticket)
    assert created["category"] == "WATER"
    assert created["location_id"] == "loc_lab_3"
    assert created["status"] == "REPORTED"
    assert created["source_channel"] == "cli"

    assert any(record["issue_id"] == ticket for record in notifications)
    assert notifications[-1]["delivered"] is True

    response_text = handler.sent[-1][1]
    assert ticket in response_text
    assert "reported" in response_text.lower()


@pytest.mark.asyncio
async def test_unknown_location_creates_no_issue(isolated_store, isolated_data_dir, monkeypatch):
    service = CampusgreenAgentService()
    _install_service(service, monkeypatch)
    handler = _new_handler()
    before, _ = _reload_issues(Path(isolated_data_dir) / "issues.json")
    seed_ids = {item["issue_id"] for item in before}

    await handler._handle_message(_text_message("There's a water leak near the old building."), {})

    issues, _ = _reload_issues(Path(isolated_data_dir) / "issues.json")
    new_ids = [item["issue_id"] for item in issues if item["issue_id"] not in seed_ids]
    assert new_ids == [], "unknown location must not create an issue"

    response_text = handler.sent[-1][1]
    assert "couldn't identify" in response_text.lower() or "could not identify" in response_text.lower()


@pytest.mark.asyncio
async def test_status_request_returns_real_stored_data(isolated_store, isolated_data_dir, monkeypatch):
    service = CampusgreenAgentService()
    _install_service(service, monkeypatch)
    handler = _new_handler()

    await handler._handle_message(_text_message("What's the status of WTR-001?"), {})

    response_text = handler.sent[-1][1]
    assert "WTR-001" in response_text
    assert "REPORTED" in response_text.upper()
    assert "Facilities Zone B" in response_text


@pytest.mark.asyncio
async def test_escalation_updates_and_notifies(isolated_store, isolated_data_dir, monkeypatch):
    service = CampusgreenAgentService()
    _install_service(service, monkeypatch)
    handler = _new_handler()

    await handler._handle_message(_text_message("There's a water leak outside Lab 3."), {})
    ticket = re.search(r"[A-Z]{3}-\d{3,}", handler.sent[-1][1]).group(0)

    await handler._handle_message(_text_message("It's getting worse — water is spreading across the floor."), {})

    issues, notifications = _reload_issues(Path(isolated_data_dir) / "issues.json")
    issue = next(item for item in issues if item["issue_id"] == ticket)
    assert issue["status"] == "ESCALATED"
    assert issue["priority"] == "CRITICAL"
    assert any(record["issue_id"] == ticket and record["notification_type"] == "escalation" for record in notifications)

    response_text = handler.sent[-1][1]
    assert "escalated" in response_text.lower()
    assert "notified" in response_text.lower()


@pytest.mark.asyncio
async def test_tool_failure_is_reported_truthfully(isolated_store, isolated_data_dir, monkeypatch):
    service = CampusgreenAgentService()
    _install_service(service, monkeypatch)
    handler = _new_handler()
    before, _ = _reload_issues(Path(isolated_data_dir) / "issues.json")
    seed_ids = {item["issue_id"] for item in before}

    await handler._handle_message(_text_message("force failure"), {})

    issues, _ = _reload_issues(Path(isolated_data_dir) / "issues.json")
    new_ids = [item["issue_id"] for item in issues if item["issue_id"] not in seed_ids]
    assert new_ids == [], "failed creation must not persist an issue"

    response_text = handler.sent[-1][1].lower()
    assert "could not create" in response_text
    assert "created" not in response_text
