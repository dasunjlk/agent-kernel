"""Phase 8 — action-planning tests for CampusGreen.

These drive ``CampusGreenDriver`` (the deterministic stand-in for the LLM's
reasoning) over the **real** CampusGreen tools, exactly as the Telegram handler
would. They verify the sustainability action-planning feature end to end:

- Plans are grounded in the recorded data (``get_sustainability_report`` counts
  plus ``search_issues`` ticket lists) and rank by the strongest real evidence,
  never by an invented scoring model or fabricated metrics.
- Every plan item carries explicit evidence (counts, open tickets, locations)
  and an operational recommended action.
- Follow-up "why" questions explain the previous plan from the recorded data.
- Plans are analysis-only: no ``update_issue``/``notify_team`` (and no new
  tickets) unless the user explicitly asks the agent to act.
- Cost/savings questions are declined without fabricating a number.
- Escalation only happens on an explicit, well-scoped request (a ticket ID or a
  describable category), and every action is verified against the persisted
  ``issues.json`` (status, priority, history, notification record).
- Empty or unavailable data yields an honest reply instead of a confident plan.

The last test is a full handler-driven scenario (plan -> why -> act -> disk
audit) mirroring ``e2e_scenario_test.py``.

All tests run against an isolated copy of the seed data and never call a live
LLM, messaging service, or network.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import test_helpers as helpers
from test_helpers import FROM_A, CampusGreenDriver, install_service, new_handler, reload_issues, text_message


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


def _reload(isolated_data_dir) -> tuple[list[dict], list[dict]]:
    return reload_issues(Path(isolated_data_dir) / "issues.json")


def _open_issues_on_disk(isolated_data_dir) -> dict[str, dict]:
    issues, _ = _reload(isolated_data_dir)
    return {item["issue_id"]: item for item in issues}


# --- General plan -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_general_plan_ranks_by_recorded_counts(isolated_store, monkeypatch):
    driver = CampusGreenDriver()
    count_calls = []
    list_calls = []
    _spy_tool(monkeypatch, "get_sustainability_report", count_calls)
    _spy_tool(monkeypatch, "search_issues", list_calls)

    reply = await _ask(driver, "What should we prioritize to improve sustainability this month?")

    assert "1. ENERGY" in reply, "ENERGY has the most recorded issues this month and must rank first"
    assert "3 recorded issue(s)" in reply, "the plan must cite the real recorded count"
    assert "ENE-002 (HIGH, IN_PROGRESS, Solar Array)" in reply, "the plan must list the actual open ticket"
    assert "Recommended action:" in reply
    assert [c[0] for c in count_calls] == ["get_sustainability_report"], "plan must gather evidence first"
    assert [c[0] for c in list_calls] == ["search_issues"], "plan must inspect the recorded issue list"


@pytest.mark.asyncio
async def test_general_plan_uses_only_data_tools(isolated_store, monkeypatch):
    calls = []
    for name in ("create_issue", "lookup_campus_location", "update_issue", "notify_team"):
        _spy_tool(monkeypatch, name, calls)
    driver = CampusGreenDriver()

    reply = await _ask(driver, "What should we prioritize this month?")

    assert reply.strip()
    assert calls == [], "an analysis plan must never create, update, or notify"


@pytest.mark.asyncio
async def test_plan_alone_makes_no_state_changes(isolated_store, isolated_data_dir):
    driver = CampusGreenDriver()
    before = _open_issues_on_disk(isolated_data_dir)

    reply = await _ask(driver, "What should we prioritize to improve sustainability this month?")
    after = _open_issues_on_disk(isolated_data_dir)

    assert "1. ENERGY" in reply
    assert set(after) == set(before), "an analysis plan must not create, modify, or resolve any ticket"
    _, notifications = _reload(isolated_data_dir)
    assert not notifications, "an analysis plan must not notify any team"


@pytest.mark.asyncio
async def test_each_plan_item_carries_evidence_and_action(isolated_store):
    driver = CampusGreenDriver()

    reply = await _ask(driver, "What should we prioritize this month?")

    items = re.findall(r"^\d\.", reply, flags=re.MULTILINE)
    assert len(items) >= 2, "the plan should present several prioritized items"
    assert len(re.findall(r"Evidence:", reply)) == len(items)
    assert len(re.findall(r"Recommended action:", reply)) == len(items)


# --- Focused plan ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_focused_plan_is_category_grounded(isolated_store):
    driver = CampusGreenDriver()

    reply = await _ask(driver, "What should we do about the recurring water issues?")

    assert "Focused on Water" in reply
    assert "2 recorded Water issue(s)" in reply
    assert "WTR-001" in reply and "WTR-002" in reply
    assert "Lab 3" in reply and "Main Library" in reply
    assert "Recommended action:" in reply


@pytest.mark.asyncio
async def test_focused_plan_reports_open_status(isolated_store):
    driver = CampusGreenDriver()

    reply = await _ask(driver, "What should we do about the water problems?")

    assert "all still open" in reply
    assert "REPORTED" in reply
    assert "Water leak outside Lab 3" in reply


@pytest.mark.asyncio
async def test_focused_plan_does_not_create_tickets(isolated_store, isolated_data_dir):
    driver = CampusGreenDriver()
    before = _open_issues_on_disk(isolated_data_dir)

    reply = await _ask(driver, "What should we do about the recurring water issues?")
    after = _open_issues_on_disk(isolated_data_dir)

    assert "WTR-001" in reply
    assert len(after) == len(before), "a water plan must not mint a new ticket"


# --- Location plan ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_location_plan_uses_report_top_locations(isolated_store):
    driver = CampusGreenDriver()

    reply = await _ask(driver, "Which campus locations should we prioritize?")

    assert "Solar Array" in reply and "Student Cafe" in reply
    assert "2 recorded issue(s)" in reply
    assert "ENE-002 (HIGH, IN_PROGRESS)" in reply
    assert "Recommended action:" in reply


# --- Follow-up "why" -------------------------------------------------------------


@pytest.mark.asyncio
async def test_why_followup_explains_the_top_priority(isolated_store):
    driver = CampusGreenDriver()

    await _ask(driver, "What should we prioritize to improve sustainability this month?")
    reply = await _ask(driver, "Why is the first one the highest priority?")

    assert "ENERGY is the top priority" in reply
    assert "3 report(s)" in reply
    assert "still open" in reply


@pytest.mark.asyncio
async def test_why_without_prior_plan_asks_for_context(isolated_store):
    driver = CampusGreenDriver()

    reply = await _ask(driver, "Why should I?")

    assert "explain" in reply.lower() or "which part" in reply.lower(), "the agent must ask instead of fabricating"


# --- Honest boundaries -----------------------------------------------------------


@pytest.mark.asyncio
async def test_cost_savings_question_declined_without_numbers(isolated_store, monkeypatch):
    calls = []
    _spy_tool(monkeypatch, "create_issue", calls)
    driver = CampusGreenDriver()

    reply = await _ask(driver, "How much money will the university save by fixing these leaks?")

    assert "can't calculate" in reply.lower() or "don't have" in reply.lower()
    assert re.search(r"\$\d", reply) is None, "the agent must not fabricate a dollar amount"
    assert re.search(r"\bsave[sd]?\s+\$?\d", reply) is None
    assert calls == [], "a savings question must not create a ticket"


@pytest.mark.asyncio
async def test_empty_data_plan_is_honest(isolated_store):
    isolated_store.issues = []
    driver = CampusGreenDriver()

    reply = await _ask(driver, "What should we prioritize to improve sustainability this month?")

    assert "no recorded" in reply.lower() or "nothing to prioritize" in reply.lower()


@pytest.mark.asyncio
async def test_empty_data_escalation_is_honest(isolated_store):
    isolated_store.issues = []
    driver = CampusGreenDriver()

    reply = await _ask(driver, "Escalate the highest-priority unresolved water issue.")

    assert "could not find an open issue" in reply.lower()


# --- Escalation on explicit request ----------------------------------------------


@pytest.mark.asyncio
async def test_escalate_top_open_category_issue_on_request(isolated_store, isolated_data_dir):
    driver = CampusGreenDriver()

    reply = await _ask(driver, "Escalate the highest-priority unresolved water issue.")

    assert "Escalated WTR-001 to Critical" in reply
    assert "Facilities Zone B" in reply and "notified" in reply.lower()

    issues, notifications = _reload(isolated_data_dir)
    wtr1 = next(item for item in issues if item["issue_id"] == "WTR-001")
    assert wtr1["status"] == "ESCALATED" and wtr1["priority"] == "CRITICAL"
    assert wtr1["history"][-1]["event"] == "escalated"
    assert any(
        n["issue_id"] == "WTR-001" and n["notification_type"] == "escalation" and n["delivered"] for n in notifications
    )


@pytest.mark.asyncio
async def test_escalate_by_ticket_id_on_request(isolated_store, isolated_data_dir):
    driver = CampusGreenDriver()

    reply = await _ask(driver, "Please escalate WTR-002 to Critical.")

    assert "Escalated WTR-002 to Critical" in reply
    assert "Facilities Zone A" in reply and "notified" in reply.lower()

    issues, notifications = _reload(isolated_data_dir)
    wtr2 = next(item for item in issues if item["issue_id"] == "WTR-002")
    assert wtr2["status"] == "ESCALATED" and wtr2["priority"] == "CRITICAL"
    assert any(n["issue_id"] == "WTR-002" and n["notification_type"] == "escalation" for n in notifications)


@pytest.mark.asyncio
async def test_escalate_without_scope_asks_for_the_issue(isolated_store, isolated_data_dir):
    driver = CampusGreenDriver()

    reply = await _ask(driver, "Escalate something urgent.")

    assert "which issue" in reply.lower() or "ticket" in reply.lower()

    issues, _ = _reload(isolated_data_dir)
    wtr1 = next(item for item in issues if item["issue_id"] == "WTR-001")
    ene2 = next(item for item in issues if item["issue_id"] == "ENE-002")
    assert wtr1["status"] == "REPORTED" and wtr1["priority"] == "HIGH", "a vague request must not act"
    assert ene2["status"] == "IN_PROGRESS", "a vague request must not act on unrelated tickets"
    _, notifications = _reload(isolated_data_dir)
    assert not any(n["notification_type"] == "escalation" for n in notifications)


# --- Full driver scenario --------------------------------------------------------


@pytest.mark.asyncio
async def test_plan_then_explain_then_act_scenario(isolated_store, isolated_data_dir):
    driver = CampusGreenDriver()

    plan = await _ask(driver, "What should we prioritize to improve sustainability this month?")
    assert "1. ENERGY" in plan
    assert "3 recorded issue(s)" in plan

    why = await _ask(driver, "Why is the first one the highest priority?")
    assert "ENERGY is the top priority" in why

    acted = await _ask(driver, "Escalate the top unresolved energy issue.")
    assert "Escalated ENE-002 to Critical" in acted
    assert "Energy & Utilities" in acted and "notified" in acted.lower()

    issues, notifications = _reload(isolated_data_dir)
    ene2 = next(item for item in issues if item["issue_id"] == "ENE-002")
    assert ene2["status"] == "ESCALATED" and ene2["priority"] == "CRITICAL"
    assert any(
        n["issue_id"] == "ENE-002" and n["notification_type"] == "escalation" and n["delivered"] for n in notifications
    )


# --- Handler-driven end-to-end ---------------------------------------------------


@pytest.mark.asyncio
async def test_action_planning_e2e_via_handler(isolated_store, isolated_data_dir, monkeypatch):
    driver = CampusGreenDriver()
    install_service(driver, monkeypatch)
    handler = new_handler()

    async def send(prompt: str) -> str:
        await handler._handle_message(text_message(prompt, from_number=FROM_A), {})
        return handler.sent[-1][1]

    reply1 = await send("What should we prioritize to improve sustainability this month?")
    assert "1. ENERGY" in reply1, "the handler reply must present the record-grounded ranking"

    reply2 = await send("Why is ENERGY ranked first?")
    assert "ENERGY is the top priority" in reply2

    reply3 = await send("Escalate the top unresolved energy issue.")
    assert "Escalated ENE-002 to Critical" in reply3
    assert "notified" in reply3.lower()

    issues, notifications = _reload(isolated_data_dir)
    ene2 = next(item for item in issues if item["issue_id"] == "ENE-002")
    assert ene2["status"] == "ESCALATED" and ene2["priority"] == "CRITICAL"
    assert any(
        n["issue_id"] == "ENE-002" and n["notification_type"] == "escalation" and n["delivered"] for n in notifications
    )
    assert len(issues) == 11, "the whole scenario must not create or remove tickets"
    assert len(handler.sent) == 3
