"""Tests for the CampusGreen WhatsApp integration layer.

These verify the Agent Kernel messaging boundary (``AgentWhatsAppRequestHandler``)
exactly as the installed Agent Kernel builds it: the handler is constructed
without running its ``__init__`` (so no Meta credentials are needed), its outbound
``_send_message`` is replaced with an in-memory recorder, and the ``AgentService``
the handler instantiates is swapped for a scripted stand-in so no real agent or
live network is touched. Helpers come from ``test_helpers.py``.

Two layers are covered:

- **Routing / session / errors** (``FakeAgentService``): an incoming WhatsApp
  text message reaches the agent service with the right prompt, ``session_id``
  (= sender number) and agent name; distinct senders get distinct sessions; the
  agent reply is sent back as a WhatsApp message; a missing agent and a raised
  error both map to friendly WhatsApp messages.
- **Tool workflows through the handler** (``CampusGreenDriver``): a deterministic
  stand-in for the LLM agent drives the **real** CampusGreen tools (``tool.py``)
  for every required scenario — report (lookup -> create -> notify), unknown
  location (no issue created), status request (get_issue), escalation
  (update + notify), truthful partial failure (created but not notified),
  duplicate reporting, and truthful tool-failure reporting.

All tests run against an isolated copy of the seed data via ``CAMPUSGREEN_DATA_DIR``
(see ``conftest.py``) and never call a live messaging service or the OpenAI API.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import test_helpers as helpers
from test_helpers import (
    FROM_A,
    FROM_B,
    CampusGreenDriver,
    FakeAgentService,
    install_service,
    new_handler,
    reload_issues,
    text_message,
)


@pytest.mark.asyncio
async def test_message_routes_text_to_agent_service(monkeypatch):
    service = FakeAgentService()
    install_service(service, monkeypatch)
    handler = new_handler()

    await handler._handle_message(text_message("There's a water leak outside Lab 3."), {})

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
    install_service(service, monkeypatch)
    handler = new_handler()

    await handler._handle_message(text_message("hello A", from_number=FROM_A), {})
    await handler._handle_message(text_message("hello B", from_number=FROM_B), {})

    assert [s for s, _ in service.selects] == [FROM_A, FROM_B]
    assert service.selects[0][0] != service.selects[1][0]


@pytest.mark.asyncio
async def test_agent_reply_is_sent_back_as_whatsapp_message(monkeypatch):
    service = FakeAgentService(reply="Ticket: WTR-001")
    install_service(service, monkeypatch)
    handler = new_handler()

    await handler._handle_message(text_message("There's a water leak outside Lab 3."), {})

    assert handler.sent
    to_number, text, reply_to = handler.sent[-1]
    assert to_number == FROM_A
    assert text == "Ticket: WTR-001"
    assert reply_to == "m1"


@pytest.mark.asyncio
async def test_missing_agent_maps_to_friendly_message(monkeypatch):
    service = FakeAgentService(agent_truthy=False)
    install_service(service, monkeypatch)
    handler = new_handler()

    await handler._handle_message(text_message(), {})

    assert handler.sent[-1][1] == "Sorry, no agent is available to handle your request."
    assert service.run_calls == [], "agent must not run when none was selected"


@pytest.mark.asyncio
async def test_generic_error_maps_to_friendly_message(monkeypatch):
    service = FakeAgentService(error=RuntimeError("agent blew up"))
    install_service(service, monkeypatch)
    handler = new_handler()

    await handler._handle_message(text_message(), {})

    assert handler.sent[-1][1] == "Sorry, there was an error processing your request."


@pytest.mark.asyncio
async def test_audio_message_rejected_before_agent(monkeypatch):
    service = FakeAgentService()
    install_service(service, monkeypatch)
    handler = new_handler()

    await handler._handle_message({"id": "m3", "from": FROM_A, "type": "audio"}, {})

    assert service.selects == []
    assert service.run_calls == []
    assert handler.sent[-1][1] == "Sorry, audio and video messages are not supported yet."


# --- Tool workflows through the handler (real tools) ---------------------------


@pytest.mark.asyncio
async def test_report_workflow_lookup_create_notify(isolated_store, isolated_data_dir, monkeypatch):
    service = CampusGreenDriver()
    install_service(service, monkeypatch)
    handler = new_handler()
    before, _ = reload_issues(Path(isolated_data_dir) / "issues.json")
    seed_ids = {item["issue_id"] for item in before}

    await handler._handle_message(text_message("There's a water leak outside Lab 3."), {})

    issues, notifications = reload_issues(Path(isolated_data_dir) / "issues.json")
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
    service = CampusGreenDriver()
    install_service(service, monkeypatch)
    handler = new_handler()
    before, _ = reload_issues(Path(isolated_data_dir) / "issues.json")
    seed_ids = {item["issue_id"] for item in before}

    await handler._handle_message(text_message("There's a water leak near the old building."), {})

    issues, _ = reload_issues(Path(isolated_data_dir) / "issues.json")
    new_ids = [item["issue_id"] for item in issues if item["issue_id"] not in seed_ids]
    assert new_ids == [], "unknown location must not create an issue"

    response_text = handler.sent[-1][1]
    assert "couldn't identify" in response_text.lower() or "could not identify" in response_text.lower()


@pytest.mark.asyncio
async def test_status_request_returns_real_stored_data(isolated_store, isolated_data_dir, monkeypatch):
    service = CampusGreenDriver()
    install_service(service, monkeypatch)
    handler = new_handler()

    await handler._handle_message(text_message("What's the status of WTR-001?"), {})

    response_text = handler.sent[-1][1]
    assert "WTR-001" in response_text
    assert "REPORTED" in response_text.upper()
    assert "Facilities Zone B" in response_text


@pytest.mark.asyncio
async def test_escalation_updates_and_notifies(isolated_store, isolated_data_dir, monkeypatch):
    service = CampusGreenDriver()
    install_service(service, monkeypatch)
    handler = new_handler()

    await handler._handle_message(text_message("There's a water leak outside Lab 3."), {})
    ticket = re.search(r"[A-Z]{3}-\d{3,}", handler.sent[-1][1]).group(0)

    await handler._handle_message(text_message("It's getting worse — water is spreading across the floor."), {})

    issues, notifications = reload_issues(Path(isolated_data_dir) / "issues.json")
    issue = next(item for item in issues if item["issue_id"] == ticket)
    assert issue["status"] == "ESCALATED"
    assert issue["priority"] == "CRITICAL"
    assert any(record["issue_id"] == ticket and record["notification_type"] == "escalation" for record in notifications)

    response_text = handler.sent[-1][1]
    assert "escalated" in response_text.lower()
    assert "notified" in response_text.lower()


@pytest.mark.asyncio
async def test_tool_failure_is_reported_truthfully(isolated_store, isolated_data_dir, monkeypatch):
    service = CampusGreenDriver()
    install_service(service, monkeypatch)
    handler = new_handler()
    before, _ = reload_issues(Path(isolated_data_dir) / "issues.json")
    seed_ids = {item["issue_id"] for item in before}

    await handler._handle_message(text_message("force failure"), {})

    issues, _ = reload_issues(Path(isolated_data_dir) / "issues.json")
    new_ids = [item["issue_id"] for item in issues if item["issue_id"] not in seed_ids]
    assert new_ids == [], "failed creation must not persist an issue"

    response_text = handler.sent[-1][1].lower()
    assert "could not create" in response_text
    assert "created" not in response_text


@pytest.mark.asyncio
async def test_partial_failure_create_ok_notify_failed(isolated_store, isolated_data_dir, monkeypatch):
    service = CampusGreenDriver()
    install_service(service, monkeypatch)
    handler = new_handler()
    before, _ = reload_issues(Path(isolated_data_dir) / "issues.json")
    seed_ids = {item["issue_id"] for item in before}

    await handler._handle_message(text_message("force notify failure"), {})

    issues, notifications = reload_issues(Path(isolated_data_dir) / "issues.json")
    new_ids = [item["issue_id"] for item in issues if item["issue_id"] not in seed_ids]
    assert len(new_ids) == 1, "partial failure: the issue itself must be created and persisted"
    assert all(
        record["issue_id"] not in new_ids for record in notifications
    ), "notification must not be recorded when delivery failed"

    response_text = handler.sent[-1][1]
    assert new_ids[0] in response_text
    assert "could not notify" in response_text.lower()

    response_lower = response_text.lower()
    assert response_lower.count("created") >= 1, "the created ticket must still be acknowledged"


@pytest.mark.asyncio
async def test_duplicate_report_creates_two_tickets(isolated_store, isolated_data_dir, monkeypatch):
    """Current V1 behavior: the same report twice produces two distinct tickets.

    Deduplication is intentionally outside SPEC.md scope and is not implemented;
    this test pins the observable behavior so it stays explicit rather than
    accidental. See TEST_REPORT.md 'Known limitations'.
    """
    service = CampusGreenDriver()
    install_service(service, monkeypatch)
    handler = new_handler()
    before, _ = reload_issues(Path(isolated_data_dir) / "issues.json")
    seed_ids = {item["issue_id"] for item in before}

    await handler._handle_message(text_message("There's a water leak outside Lab 3."), {})
    await handler._handle_message(text_message("There's still a water leak outside Lab 3."), {})

    issues, _ = reload_issues(Path(isolated_data_dir) / "issues.json")
    new_ids = [item["issue_id"] for item in issues if item["issue_id"] not in seed_ids]
    assert len(new_ids) == 2, "V1 allows duplicate reports; both reports create a ticket"
    assert len(set(new_ids)) == 2, "duplicate reports must produce distinct ticket IDs"


@pytest.mark.asyncio
async def test_multi_turn_status_resolves_active_issue(isolated_store, isolated_data_dir, monkeypatch):
    service = CampusGreenDriver()
    install_service(service, monkeypatch)
    handler = new_handler()

    await handler._handle_message(text_message("There's a water leak outside Lab 3."), {})
    ticket = re.search(r"[A-Z]{3}-\d{3,}", handler.sent[-1][1]).group(0)

    await handler._handle_message(text_message("What's the current status?"), {})

    response_text = handler.sent[-1][1]
    assert ticket in response_text, "a follow-up status question must resolve to the session's active issue"
    assert "REPORTED" in response_text.upper()
