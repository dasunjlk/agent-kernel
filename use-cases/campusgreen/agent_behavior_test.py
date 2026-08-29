"""Agent-behavior tests for CampusGreen.

These drive ``CampusGreenDriver`` (the deterministic stand-in for the LLM) over
the **real** CampusGreen tools exactly as the WhatsApp handler would: a prompt in,
a transcript out, with tool calls recorded so the assertions can verify *what the
agent actually did* — which tools it called, with which arguments, and whether it
reported successes and failures truthfully.

This is the runnable half of the agent-reliability proof. The
LLM-dependent half (real OpenAI responses to the same prompts) lives in
``demo_test.py`` and is gated on ``OPENAI_API_KEY``; both halves cover the same
scenarios so the deterministic tier is a faithful offline stand-in.
"""

from __future__ import annotations

import pytest

import test_helpers as helpers
from test_helpers import CampusGreenDriver


class _Req:
    def __init__(self, prompt: str):
        self.prompt = prompt


def _spy_tool(monkeypatch, name: str, calls: list):
    original = getattr(helpers, name)

    def wrapper(*args, **kwargs):
        calls.append((name, args, kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(helpers, name, wrapper)


async def _ask(driver: CampusGreenDriver, prompt: str, session_id: str = "sess-A") -> str:
    driver.select(session_id=session_id)
    reply = await driver.run_multi([_Req(prompt)])
    return reply.response


@pytest.mark.asyncio
async def test_general_question_uses_no_tools(isolated_store, monkeypatch):
    calls = []
    for name in (
        "create_issue",
        "lookup_campus_location",
        "get_issue",
        "update_issue",
        "notify_team",
        "get_sustainability_report",
    ):
        _spy_tool(monkeypatch, name, calls)
    driver = CampusGreenDriver()

    reply = await _ask(driver, "What can you help me with?")

    assert "campus sustainability" in reply.lower() or "coordinate" in reply.lower()
    assert calls == [], "a general question must not trigger any tool call"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("prompt", "category", "priority", "location_id"),
    [
        ("There's a water leak outside Lab 3.", "WATER", "HIGH", "loc_lab_3"),
        ("A dripping tap in the Main Library restroom", "WATER", "MEDIUM", "loc_main_library"),
        ("Corridor lighting running all day in the Physics Wing", "ENERGY", "HIGH", "loc_physics_wing"),
        ("A light is on in Lab 4 during the day.", "ENERGY", "MEDIUM", "loc_lab_4"),
        (
            "The bins near the Student Cafe entrance are overflowing and blocking the walkway.",
            "WASTE",
            "HIGH",
            "loc_student_cafe",
        ),
        ("Litter scattered around the Central Plaza", "WASTE", "MEDIUM", "loc_central_plaza"),
        ("Smoke and a burning smell near the North Gate", "POLLUTION", "HIGH", "loc_north_gate"),
        ("The cafeteria discarded unsold meals again", "FOOD", "MEDIUM", "loc_student_cafe"),
        ("A damaged solar panel mounting bracket at the Solar Array", "INFRASTRUCTURE", "MEDIUM", "loc_solar_array"),
    ],
)
async def test_report_resolves_category_priority_location(
    isolated_store, monkeypatch, prompt, category, priority, location_id
):
    calls = []
    _spy_tool(monkeypatch, "lookup_campus_location", calls)
    _spy_tool(monkeypatch, "create_issue", calls)
    _spy_tool(monkeypatch, "notify_team", calls)
    driver = CampusGreenDriver()

    reply = await _ask(driver, prompt)

    assert "reported" in reply.lower()
    assert "Ticket:" in reply
    create_args = [call for call in calls if call[0] == "create_issue"]
    assert len(create_args) == 1, "exactly one create per report"
    _, args, kwargs = create_args[0]
    assert kwargs["category"] == category
    assert kwargs["location_id"] == location_id
    assert kwargs["priority"] == priority


@pytest.mark.asyncio
async def test_status_by_explicit_id_uses_stored_data(isolated_store, monkeypatch):
    calls = []
    _spy_tool(monkeypatch, "get_issue", calls)
    driver = CampusGreenDriver()

    reply = await _ask(driver, "What's the status of WTR-001?")

    assert [call[0] for call in calls] == ["get_issue"]
    assert "WTR-001" in reply
    assert "REPORTED" in reply.upper()
    assert "Facilities Zone B" in reply


@pytest.mark.asyncio
async def test_unknown_issue_is_answered_without_hallucination(isolated_store):
    driver = CampusGreenDriver()

    reply = await _ask(driver, "What's the status of WTR-999?")

    assert "could not find issue wtr-999" in reply.lower()
    assert "Status:" not in reply, "the agent must not fabricate a status for an unknown issue"


@pytest.mark.asyncio
async def test_unknown_location_clarifies_and_creates_nothing(isolated_store, monkeypatch):
    calls = []
    _spy_tool(monkeypatch, "create_issue", calls)
    driver = CampusGreenDriver()

    reply = await _ask(driver, "There's a water leak near the old building.")

    assert "couldn't identify" in reply.lower() or "could not identify" in reply.lower()
    assert calls == [], "an unknown location must never create an issue"


@pytest.mark.asyncio
async def test_missing_information_triggers_questions(isolated_store, monkeypatch):
    calls = []
    _spy_tool(monkeypatch, "create_issue", calls)
    _spy_tool(monkeypatch, "lookup_campus_location", calls)
    driver = CampusGreenDriver()

    reply = await _ask(driver, "There is a problem I want to report.")

    assert "what type of problem" in reply.lower()
    assert calls == [], "no tool may run without a category and a location"


@pytest.mark.asyncio
async def test_multi_turn_escalation_continuity(isolated_store, monkeypatch):
    calls = []
    _spy_tool(monkeypatch, "update_issue", calls)
    _spy_tool(monkeypatch, "notify_team", calls)
    driver = CampusGreenDriver()

    first = await _ask(driver, "There's a water leak outside Lab 3.")
    ticket = first.split("Ticket: ")[1].split("\n")[0]

    escalated = await _ask(driver, "It's getting worse — water is spreading across the floor.")
    assert "escalated" in escalated.lower()
    updates = [call for call in calls if call[0] == "update_issue"]
    assert len(updates) == 1, "escalation must update the ticket exactly once"
    args, kwargs = updates[0][1], updates[0][2]
    assert args and args[0] == ticket
    assert kwargs["status"] == "ESCALATED"
    assert kwargs["priority"] == "CRITICAL"

    status = await _ask(driver, "What's the current status?")
    assert ticket in status
    assert "ESCALATED" in status.upper()
    assert "CRITICAL" in status.upper()


@pytest.mark.asyncio
async def test_session_isolation_between_senders(isolated_store):
    driver = CampusGreenDriver()

    await _ask(driver, "There's a water leak outside Lab 3.", session_id="sender-A")

    reply_b = await _ask(driver, "What's the current status?", session_id="sender-B")
    assert "which issue you mean" in reply_b.lower()

    reply_a = await _ask(driver, "What's the current status?", session_id="sender-A")
    assert "Ticket:" in reply_a
    assert "REPORTED" in reply_a.upper() or "ESCALATED" in reply_a.upper()


@pytest.mark.asyncio
async def test_partial_failure_reports_created_but_not_notified(isolated_store, monkeypatch):
    driver = CampusGreenDriver()

    reply = await _ask(driver, "force notify failure")

    assert "was created" in reply.lower()
    assert "could not notify" in reply.lower()
    assert "notified the responsible team" not in reply.lower()


@pytest.mark.asyncio
async def test_failed_create_is_not_claimed(isolated_store, monkeypatch):
    driver = CampusGreenDriver()

    reply = await _ask(driver, "force failure")

    assert "could not create" in reply.lower()
    assert "created" not in reply.lower()


@pytest.mark.asyncio
async def test_analytics_are_computed_from_real_records(isolated_store):
    driver = CampusGreenDriver()

    reply = await _ask(driver, "What are the biggest sustainability problems this month?")

    assert "recorded issue" in reply.lower()
    assert "ENERGY" in reply.upper()


@pytest.mark.asyncio
async def test_team_notification_claim_reflects_records(isolated_store):
    driver = CampusGreenDriver()

    await _ask(driver, "There's a water leak outside Lab 3.")
    reply = await _ask(driver, "Has the team been notified?")

    assert "yes" in reply.lower()
    assert "Facilities Zone B" in reply
    assert "was notified" in reply.lower()


@pytest.mark.asyncio
async def test_duplicate_report_yields_two_distinct_tickets(isolated_store):
    driver = CampusGreenDriver()

    first = await _ask(driver, "There's a water leak outside Lab 3.")
    ticket_a = first.split("Ticket: ")[1].split("\n")[0]

    second = await _ask(driver, "There's still a water leak outside Lab 3.")
    ticket_b = second.split("Ticket: ")[1].split("\n")[0]

    assert ticket_a != ticket_b
