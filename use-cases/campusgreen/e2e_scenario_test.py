"""End-to-end competition scenario for CampusGreen.

This is the closest thing to the real product experience that runs offline: a
single Telegram sender walks through the full journey against the **real** tools
and real persisted data, with the deterministic agent stand-in doing the
reasoning. After every step the test asserts both the transcript the user saw
and the state actually written to ``issues.json``, so nothing in the reply is
ever unverifiable.

Scenario (11 steps):

    1. report a water leak outside Lab 3  -> ticket created + notified
    2. follow up on "current status"      -> resolves to the session's issue
    3. ask the biggest problems this month -> real counts from recorded issues
    4. report overflowing bins near the Student Cafe -> second ticket
    5. "has the team been notified?"      -> answered from recorded notifications
    6. status by explicit ticket ID       -> real stored state
    7. worsening report                   -> escalation (status + priority + history)
    8. re-notify the team on escalation   -> escalation notification recorded
    9. current status after escalation    -> ESCALATED / CRITICAL
   10. partial failure probe              -> ticket created but notify fails, truthfully reported
   11. final persisted-state audit       -> every claim checks out on disk
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import pytest_asyncio

import test_helpers as helpers
from test_helpers import FROM_A, CampusGreenDriver, install_service, new_handler, reload_issues, text_message


def _reload(isolated_data_dir) -> dict:
    raw = json.loads((Path(isolated_data_dir) / "issues.json").read_text(encoding="utf-8"))
    return {item["issue_id"]: item for item in raw["issues"]}, raw["notifications"]


@pytest.mark.asyncio
async def test_competition_scenario_end_to_end(isolated_store, isolated_data_dir, monkeypatch):
    driver = CampusGreenDriver()
    install_service(driver, monkeypatch)
    handler = new_handler()

    async def send(prompt: str) -> str:
        await handler._handle_message(text_message(prompt, from_number=FROM_A), {})
        return handler.sent[-1][1]

    # --- Step 1: report a water leak outside Lab 3 ---------------------------
    reply1 = await send("There's a water leak outside Lab 3.")
    assert "Ticket:" in reply1 and "reported" in reply1.lower()
    assert "Facilities Zone B" in reply1
    ticket = re.search(r"Ticket: (\w+-\d{3,})", reply1).group(1)
    issues, notifications = _reload(isolated_data_dir)
    assert ticket in issues
    assert issues[ticket]["category"] == "WATER"
    assert issues[ticket]["location_id"] == "loc_lab_3"
    assert issues[ticket]["priority"] == "HIGH"
    assert issues[ticket]["status"] == "REPORTED"
    assert issues[ticket]["assigned_team_id"] == "team_facilities_zone_b"
    assert issues[ticket]["source_channel"] == "cli"
    assert any(
        n["issue_id"] == ticket and n["notification_type"] == "new_issue" and n["delivered"] for n in notifications
    )

    # --- Step 2: follow-up "current status" resolves to the session's issue ---
    reply2 = await send("What's the current status?")
    assert ticket in reply2
    assert "REPORTED" in reply2.upper()
    issues, _ = _reload(isolated_data_dir)
    assert issues[ticket]["status"] == "REPORTED"

    # --- Step 3: biggest problems this month (real counts) -------------------
    reply3 = await send("What are the biggest sustainability problems this month?")
    assert "most common recorded issue" in reply3
    assert "WATER" in reply3.upper() or "ENERGY" in reply3.upper()
    issues, _ = _reload(isolated_data_dir)
    assert len(issues) == 12  # 11 seeded + the one just created

    # --- Step 4: second report, different category ---------------------------
    reply4 = await send("The bins near the Student Cafe are overflowing and blocking the walkway.")
    ticket_u = re.search(r"Ticket: (\w+-\d{3,})", reply4).group(1)
    assert ticket_u != ticket, "a distinct report must produce a distinct ticket"
    assert "Catering Services" in reply4
    issues, notifications = _reload(isolated_data_dir)
    assert issues[ticket_u]["category"] == "WASTE"
    assert issues[ticket_u]["location_id"] == "loc_student_cafe"
    assert issues[ticket_u]["priority"] == "HIGH"
    assert any(n["issue_id"] == ticket_u and n["notification_type"] == "new_issue" for n in notifications)

    # --- Step 5: "has the team been notified?" from recorded state -----------
    reply5 = await send("Has the team been notified?")
    assert ticket_u in reply5
    assert "Catering Services" in reply5 and "was notified" in reply5

    # --- Step 6: status by explicit ticket ID --------------------------------
    reply6 = await send(f"What's the status of {ticket}?")
    assert ticket in reply6
    assert "REPORTED" in reply6.upper()
    assert "Facilities Zone B" in reply6

    # --- Step 7: worsening report -> escalation ------------------------------
    reply7 = await send("The leak is getting worse and water is spreading across the floor.")
    assert "escalated to Critical" in reply7
    issues, notifications = _reload(isolated_data_dir)
    assert issues[ticket]["status"] == "ESCALATED"
    assert issues[ticket]["priority"] == "CRITICAL"
    assert issues[ticket]["history"][-1]["event"] == "escalated"
    assert any(n["issue_id"] == ticket and n["notification_type"] == "escalation" for n in notifications)

    # --- Step 8: re-notify on escalation actually recorded -------------------
    issues, notifications = _reload(isolated_data_dir)
    escalation_records = [
        n for n in notifications if n["issue_id"] == ticket and n["notification_type"] == "escalation"
    ]
    assert len(escalation_records) == 1
    assert escalation_records[0]["delivered"] is True

    # --- Step 9: current status after escalation -----------------------------
    reply9 = await send("What's the current status?")
    assert ticket in reply9
    assert "ESCALATED" in reply9.upper()
    assert "CRITICAL" in reply9.upper()

    # --- Step 10: partial failure, reported truthfully -----------------------
    reply10 = await send("force notify failure")
    ticket_v = re.search(r"Ticket:? (\w+-\d{3,})", reply10).group(1)
    assert "was created" in reply10.lower()
    assert "could not notify" in reply10.lower()
    issues, notifications = _reload(isolated_data_dir)
    assert issues[ticket_v]["status"] == "REPORTED"
    assert not any(n["issue_id"] == ticket_v for n in notifications), "failed delivery must not be recorded"

    # --- Step 11: final persisted-state audit --------------------------------
    issues, notifications = _reload(isolated_data_dir)
    assert len(issues) == 14  # 11 seeded + water ticket + waste ticket + partial-failure ticket
    assert {issues[t]["status"] for t in (ticket, ticket_u, ticket_v)} == {"ESCALATED", "REPORTED"}
    expected_types = {n["issue_id"]: n["notification_type"] for n in notifications}
    assert expected_types[ticket] == "escalation"
    assert expected_types[ticket_u] == "new_issue"
    assert ticket_v not in expected_types
    assert all(n["delivered"] for n in notifications), "only successful deliveries are recorded"
    assert all(n["issue_id"] in issues for n in notifications), "every notification must reference a live issue"
    # The full conversation was persisted to the committed-format JSON file.
    raw = json.loads((Path(isolated_data_dir) / "issues.json").read_text(encoding="utf-8"))
    assert len(raw["issues"]) == len(issues)
    # Transcript integrity: 11 narrative steps across 9 user messages.
    assert len(handler.sent) == 9
