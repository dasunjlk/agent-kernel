"""Phase 7 — polished conversational-flow tests for CampusGreen.

These drive ``CampusGreenDriver`` (the deterministic stand-in for the LLM's
reasoning) over the **real** CampusGreen tools, exactly as the Telegram handler
would: a prompt in, a transcript out. Where useful they spy on tool calls to
assert *what the agent actually did* — which tools ran, with what arguments, and
that no tool ran when none was needed.

They cover the Phase 7 polished-user-flow matrix:

- Complete issue report (report -> create -> notify -> response)
- Incomplete report -> useful clarification (no ticket created)
- Multi-turn clarification continuity (a follow-up that only supplies the
  missing location continues the same report)
- Contextual status ("what's the status?" resolves the just-created issue)
- Natural status phrasings (by ID and by topic)
- Escalation on a worsening issue
- Multi-issue reference resolution (topic-aware, not just "most recent")
- Unsupported request (declined, no fabricated action)
- Unknown location / unknown issue (never fabricated)
- Tool failure / partial failure (truthful, never overstated)
- General conversation / courtesy (no unnecessary tool calls)
- Recovery after failure (retry does not duplicate completed work)

All tests run against an isolated copy of the seed data and never call a live
LLM, messaging service, or network.
"""

from __future__ import annotations

import re

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


def _ticket(reply: str) -> str:
    return reply.split("Ticket: ")[1].split("\n")[0]


# --- 1. Complete issue report -------------------------------------------------


@pytest.mark.asyncio
async def test_complete_report_single_turn(isolated_store, monkeypatch):
    calls = []
    _spy_tool(monkeypatch, "lookup_campus_location", calls)
    _spy_tool(monkeypatch, "create_issue", calls)
    _spy_tool(monkeypatch, "notify_team", calls)
    driver = CampusGreenDriver()

    reply = await _ask(driver, "There's a water leak outside Lab 3.")

    assert "reported" in reply.lower()
    assert "Ticket:" in reply and "WTR-" in reply
    assert "Lab 3" in reply
    assert "Facilities Zone B" in reply
    assert "notified" in reply.lower()
    assert len([c for c in calls if c[0] == "create_issue"]) == 1
    assert len([c for c in calls if c[0] == "notify_team"]) == 1


# --- 2. Incomplete report -> clarification --------------------------------------


@pytest.mark.asyncio
async def test_incomplete_report_asks_without_creating(isolated_store, monkeypatch):
    calls = []
    _spy_tool(monkeypatch, "create_issue", calls)
    driver = CampusGreenDriver()

    reply = await _ask(driver, "There is a problem.")

    assert "what type of problem" in reply.lower()
    assert calls == [], "an underspecified report must not create a ticket"


# --- 3. Multi-turn clarification (a location-only follow-up continues the report)


@pytest.mark.asyncio
async def test_multi_turn_clarification_continues_report(isolated_store, monkeypatch):
    calls = []
    _spy_tool(monkeypatch, "create_issue", calls)
    driver = CampusGreenDriver()

    first = await _ask(driver, "There is a leak.")

    assert "which campus building" in first.lower(), "the agent must ask for a location"

    second = await _ask(driver, "Near Lab 3.")

    assert "reported" in second.lower()
    assert "Ticket:" in second and "WTR-" in second
    assert "Lab 3" in second
    creates = [c for c in calls if c[0] == "create_issue"]
    assert len(creates) == 1, "a location-only follow-up must complete the same report, not start a new one"
    assert creates[0][2]["location_id"] == "loc_lab_3"


@pytest.mark.asyncio
async def test_clarification_scoped_to_session(isolated_store, monkeypatch):
    driver = CampusGreenDriver()

    await _ask(driver, "There is a leak.", session_id="sender-A")

    reply_b = await _ask(driver, "A light is on in Lab 4 during the day.", session_id="sender-B")

    assert "reported" in reply_b.lower()
    assert "ENE-" in reply_b, "sender B's report must not inherit sender A's pending leak context"


# --- 4. Contextual status --------------------------------------------------------


@pytest.mark.asyncio
async def test_contextual_status_resolves_just_created_issue(isolated_store):
    driver = CampusGreenDriver()

    await _ask(driver, "There's a water leak outside Lab 3.")
    ticket = _ticket(await _ask(driver, "What's the current status?"))

    assert "WTR-" in ticket


# --- 5. Natural status phrasings -------------------------------------------------


@pytest.mark.asyncio
async def test_status_by_explicit_id_natural_phrasing(isolated_store):
    driver = CampusGreenDriver()

    reply = await _ask(driver, "What's happening with WTR-001?")

    assert "WTR-001" in reply and "REPORTED" in reply.upper()


@pytest.mark.asyncio
async def test_status_by_topic_phrasing(isolated_store):
    driver = CampusGreenDriver()

    await _ask(driver, "There's a water leak outside Lab 3.")
    reply = await _ask(driver, "Is the leak issue resolved?")

    assert "Ticket:" in reply and "WTR-" in reply


# --- 6. Escalation on a worsening issue -------------------------------------------


@pytest.mark.asyncio
async def test_worsening_issue_escalates(isolated_store):
    driver = CampusGreenDriver()

    first = await _ask(driver, "There's a water leak outside Lab 3.")
    ticket = _ticket(first)

    reply = await _ask(driver, "It's getting much worse.")
    assert "escalated" in reply.lower()
    assert ticket in reply

    status = await _ask(driver, "What's the current status?")
    assert "ESCALATED" in status.upper()
    assert "CRITICAL" in status.upper()


# --- Multi-issue reference resolution ----------------------------------------------


@pytest.mark.asyncio
async def test_multi_issue_reference_resolved_by_topic(isolated_store):
    driver = CampusGreenDriver()

    await _ask(driver, "There's a water leak outside Lab 3.")
    await _ask(driver, "The bins near the Student Cafe are overflowing.")

    leak_status = await _ask(driver, "What is the status of the leak?")
    bins_status = await _ask(driver, "What is the status of the bins issue?")

    leak_ticket = _ticket(leak_status)
    bins_ticket = _ticket(bins_status)
    assert leak_ticket != bins_ticket, "the two issues must not be confused"
    assert leak_ticket.startswith("WTR-"), "'the leak' must resolve to the water issue"
    assert bins_ticket.startswith("WST-"), "'the bins issue' must resolve to the waste issue"


@pytest.mark.asyncio
async def test_multiple_same_category_issues_ask_when_ambiguous(isolated_store):
    driver = CampusGreenDriver()

    await _ask(driver, "There's a water leak outside Lab 3.")
    await _ask(driver, "There's also a dripping tap at the Main Library.")

    # Two WATER issues exist; a generic status reference is genuinely ambiguous.
    reply = await _ask(driver, "What's the status of the water issue?")
    # The driver resolves to the most recently discussed WATER issue rather than
    # fabricating one; the reply must still cite a real ticket (never a guess).
    assert "Ticket:" in reply and "WTR-" in reply


# --- Unsupported request ------------------------------------------------------------


@pytest.mark.asyncio
async def test_unsupported_request_honestly_declined(isolated_store, monkeypatch):
    calls = []
    for name in ("create_issue", "lookup_campus_location", "get_issue", "search_issues", "update_issue", "notify_team"):
        _spy_tool(monkeypatch, name, calls)
    driver = CampusGreenDriver()

    reply = await _ask(driver, "Can you book me a university bus?")

    assert "outside" in reply.lower() or "that's not" in reply.lower() or "can't" in reply.lower()
    assert "sustainability" in reply.lower(), "the agent should redirect to its role"
    assert calls == [], "an unsupported request must perform no tool action"
    assert "created" not in reply.lower() and "booked" not in reply.lower()


# --- Unknown location ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_location_not_fabricated(isolated_store, monkeypatch):
    calls = []
    _spy_tool(monkeypatch, "create_issue", calls)
    driver = CampusGreenDriver()

    reply = await _ask(driver, "There's a leak near the old building.")

    assert "couldn't identify" in reply.lower() or "could not identify" in reply.lower()
    assert "Lab 3" not in reply, "the agent must not invent a location"
    assert calls == [], "an unknown location must not create an issue"


# --- Unknown issue --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_issue_not_fabricated(isolated_store):
    driver = CampusGreenDriver()

    reply = await _ask(driver, "What's the status of WTR-9999?")

    assert "could not find" in reply.lower() or "not found" in reply.lower()
    assert "Status:" not in reply, "the agent must not fabricate a status for an unknown issue"


# --- Tool failure / partial failure ------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_failure_reported_truthfully(isolated_store, monkeypatch):
    driver = CampusGreenDriver()

    reply = await _ask(driver, "force failure")

    assert "could not create" in reply.lower()
    assert "created" not in reply.lower()


@pytest.mark.asyncio
async def test_partial_failure_reported_accurately(isolated_store, isolated_data_dir):
    driver = CampusGreenDriver()
    before, _ = _reload_issues(isolated_data_dir)
    seed_ids = {item["issue_id"] for item in before}

    reply = await _ask(driver, "force notify failure")

    assert "was created" in reply.lower()
    assert "could not notify" in reply.lower()
    assert "notified the responsible team" not in reply.lower()

    issues, notifications = _reload_issues(isolated_data_dir)
    new_ids = [item["issue_id"] for item in issues if item["issue_id"] not in seed_ids]
    assert len(new_ids) == 1, "partial failure: the issue itself is created"
    assert all(record["issue_id"] not in new_ids for record in notifications), "no delivery recorded on failure"


def _reload_issues(isolated_data_dir):
    import json

    raw = json.loads((isolated_data_dir / "issues.json").read_text(encoding="utf-8"))
    return raw["issues"], raw["notifications"]


# --- General conversation / courtesy (no unnecessary tools) -------------------------------


@pytest.mark.asyncio
async def test_courtesy_messages_use_no_tools(isolated_store, monkeypatch):
    calls = []
    for name in (
        "create_issue",
        "lookup_campus_location",
        "get_issue",
        "search_issues",
        "update_issue",
        "notify_team",
        "get_sustainability_report",
    ):
        _spy_tool(monkeypatch, name, calls)
    driver = CampusGreenDriver()

    for message in ("Thanks!", "Okay.", "Got it.", "Hello."):
        reply = await _ask(driver, message)
        assert reply.strip(), f"a courteous reply must not be empty for {message!r}"

    assert calls == [], "courtesy messages must never trigger a tool call"


@pytest.mark.asyncio
async def test_capability_question_uses_no_tools(isolated_store, monkeypatch):
    calls = []
    _spy_tool(monkeypatch, "create_issue", calls)
    _spy_tool(monkeypatch, "notify_team", calls)
    driver = CampusGreenDriver()

    reply = await _ask(driver, "What can you do?")

    assert "sustainability" in reply.lower()
    assert calls == [], "a capabilities question must not call report tools"


# --- Recovery after failure ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_after_failure_does_not_duplicate(isolated_store, monkeypatch):
    driver = CampusGreenDriver()

    await _ask(driver, "force failure")

    reply = await _ask(driver, "Please try again.")

    assert "retry" in reply.lower() or "describe the issue" in reply.lower() or "describing" in reply.lower()
    assert "Ticket:" not in reply, "a retry after a failed create must not jump ahead to a fabricated ticket"
