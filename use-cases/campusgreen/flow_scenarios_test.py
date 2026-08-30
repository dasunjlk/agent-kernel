"""Phase 7 — polished user-flow scenarios through the real WhatsApp surface.

These replay whole conversations one realistic prompt at a time through the
*actual* ``WhatsAppHandler`` pipeline (harness stand-in ``CampusGreenDriver``
does the reasoning, the real tools and real persisted ``issues.json`` do the
work). Each scenario asserts both the transcript the user saw **and** the state
that was actually written to disk, so every claim in a reply is verifiable.

Seven scenarios (Flow matrix §34):

    1. Complete report        — one cadence to a ticket + notification
    2. Incomplete report      — useful clarification, no ticket created
    3. Multi-turn clarification — a location-only follow-up completes the report
    4. Contextual status      — "current status?" resolves to the just-created issue
    5. Escalation             — a worsening report escalates truthfully
    6. Sustainability report  — real counts from recorded issues
    7. Unknown location       — no fabricated ticket or location
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import test_helpers as helpers
from test_helpers import FROM_A, CampusGreenDriver, install_service, new_handler, text_message


def _issues(isolated_data_dir) -> list[dict]:
    raw = json.loads((Path(isolated_data_dir) / "issues.json").read_text(encoding="utf-8"))
    return raw["issues"]


# --- 1. Complete report -----------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_complete_report(isolated_store, isolated_data_dir, monkeypatch):
    driver = CampusGreenDriver()
    install_service(driver, monkeypatch)
    handler = new_handler()

    await handler._handle_message(text_message("There's a water leak outside Lab 3.", from_number=FROM_A), {})
    reply = handler.sent[-1][1]

    assert "Ticket:" in reply and "reported" in reply.lower()
    assert "Facilities Zone B" in reply and "notified" in reply.lower()
    ticket = re.search(r"Ticket: (\w+-\d{3,})", reply).group(1)

    issues = _issues(isolated_data_dir)
    record = next(item for item in issues if item["issue_id"] == ticket)
    assert record["category"] == "WATER"
    assert record["location_id"] == "loc_lab_3"
    assert record["priority"] == "HIGH"
    assert record["status"] == "REPORTED"
    assert record["assigned_team_id"] == "team_facilities_zone_b"


# --- 2. Incomplete report -> clarification (no ticket) ------------------------------


@pytest.mark.asyncio
async def test_scenario_incomplete_report_clarifies(isolated_store, isolated_data_dir, monkeypatch):
    driver = CampusGreenDriver()
    install_service(driver, monkeypatch)
    handler = new_handler()
    before = len(_issues(isolated_data_dir))

    await handler._handle_message(text_message("I want to report a problem.", from_number=FROM_A), {})
    reply = handler.sent[-1][1]

    assert "what type of problem" in reply.lower()
    assert len(_issues(isolated_data_dir)) == before, "no ticket may be created from an underspecified report"


# --- 3. Multi-turn clarification continuity ------------------------------------------


@pytest.mark.asyncio
async def test_scenario_multi_turn_clarification(isolated_store, isolated_data_dir, monkeypatch):
    driver = CampusGreenDriver()
    install_service(driver, monkeypatch)
    handler = new_handler()
    before = len(_issues(isolated_data_dir))

    await handler._handle_message(text_message("There is a leak.", from_number=FROM_A), {})
    first = handler.sent[-1][1]
    assert "which campus building" in first.lower()
    assert len(_issues(isolated_data_dir)) == before, "location missing -> nothing created yet"

    await handler._handle_message(text_message("Near Lab 3.", from_number=FROM_A), {})
    second = handler.sent[-1][1]
    assert "Ticket:" in second and "reported" in second.lower()
    assert "Lab 3" in second

    issues = _issues(isolated_data_dir)
    new_records = [item for item in issues if item["issue_id"] not in {x["issue_id"] for x in issues[:before]}]
    assert len(new_records) == 1, "a location-only follow-up must complete one report, not start another"
    assert new_records[0]["category"] == "WATER"
    assert new_records[0]["location_id"] == "loc_lab_3"


# --- 4. Contextual status -------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_contextual_status(isolated_store, isolated_data_dir, monkeypatch):
    driver = CampusGreenDriver()
    install_service(driver, monkeypatch)
    handler = new_handler()

    await handler._handle_message(text_message("There's a water leak outside Lab 3.", from_number=FROM_A), {})
    ticket = re.search(r"Ticket: (\w+-\d{3,})", handler.sent[-1][1]).group(1)

    await handler._handle_message(text_message("What's the current status?", from_number=FROM_A), {})
    reply = handler.sent[-1][1]
    assert ticket in reply and "REPORTED" in reply.upper()

    record = next(item for item in _issues(isolated_data_dir) if item["issue_id"] == ticket)
    assert record["status"] == "REPORTED"


# --- 5. Escalation ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_escalation(isolated_store, isolated_data_dir, monkeypatch):
    driver = CampusGreenDriver()
    install_service(driver, monkeypatch)
    handler = new_handler()

    await handler._handle_message(text_message("There's a water leak outside Lab 3.", from_number=FROM_A), {})
    ticket = re.search(r"Ticket: (\w+-\d{3,})", handler.sent[-1][1]).group(1)

    await handler._handle_message(text_message("The leak is getting worse and spreading.", from_number=FROM_A), {})
    reply = handler.sent[-1][1]
    assert "escalated" in reply.lower()

    record = next(item for item in _issues(isolated_data_dir) if item["issue_id"] == ticket)
    assert record["status"] == "ESCALATED"
    assert record["priority"] == "CRITICAL"
    assert record["history"][-1]["event"] == "escalated"


# --- 6. Sustainability report (real counts) ----------------------------------------------


@pytest.mark.asyncio
async def test_scenario_sustainability_report(isolated_store, isolated_data_dir, monkeypatch):
    driver = CampusGreenDriver()
    install_service(driver, monkeypatch)
    handler = new_handler()

    await handler._handle_message(
        text_message("What are the biggest sustainability problems this month?", from_number=FROM_A), {}
    )
    reply = handler.sent[-1][1]
    assert "most common recorded issue" in reply
    assert "WATER" in reply.upper() or "ENERGY" in reply.upper()

    issues = _issues(isolated_data_dir)
    assert len(issues) == 11, "a read-only report must not create or alter issues"


# --- 7. Unknown location (no fabrication) ------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_unknown_location(isolated_store, isolated_data_dir, monkeypatch):
    driver = CampusGreenDriver()
    install_service(driver, monkeypatch)
    handler = new_handler()
    before = len(_issues(isolated_data_dir))

    await handler._handle_message(
        text_message("There's a leak near the old building next to the quarry.", from_number=FROM_A), {}
    )
    reply = handler.sent[-1][1]
    assert "couldn't identify" in reply.lower() or "could not identify" in reply.lower()
    assert "Lab 3" not in reply, "the agent must not invent a location"

    assert len(_issues(isolated_data_dir)) == before, "an unknown location must not create an issue"
    raw = json.loads((Path(isolated_data_dir) / "issues.json").read_text(encoding="utf-8"))
    assert len(raw["issues"]) == before
