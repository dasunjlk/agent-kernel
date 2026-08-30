"""Unit tests for the CampusGreen tools (data layer + deterministic logic).

Covers the full acceptance matrix: location lookup, issue creation / retrieval /
update (including the SPEC section 13 lifecycle state machine), team
notification, sustainability reports, defensive validation of non-string tool
inputs, the per-session active-issue cache, and no-write-on-failure guarantees.

All tests run against a pristine, isolated copy of the seed data via the
``isolated_store`` fixture (see ``conftest.py``).
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

import pytest
from agentkernel.core import Session, ToolContext

import tool as campus_tool
from tool import (
    ALLOWED_TRANSITIONS,
    CATEGORIES,
    ISSUE_ID_RE,
    NOTIFICATION_TYPES,
    PRIORITIES,
    STATUSES,
    create_issue,
    get_issue,
    get_sustainability_report,
    lookup_campus_location,
    notify_team,
    search_issues,
    update_issue,
)


def _within_session(session_id: str, fn):
    ctx = ToolContext(runtime=None, agent=None, session=Session(session_id), requests=[])
    ctx.set()
    try:
        return fn()
    finally:
        ctx.reset()


# --- lookup_campus_location --------------------------------------------------


def test_lookup_by_display_name(isolated_store):
    result = lookup_campus_location("Lab 3")
    assert result["status"] == "ok"
    assert result["location"]["location_id"] == "loc_lab_3"
    assert result["location"]["responsible_team_id"] == "team_facilities_zone_b"


def test_lookup_by_alias_case_insensitive(isolated_store):
    result = lookup_campus_location("COMPUTER LAB 3")
    assert result["status"] == "ok"
    assert result["location"]["location_id"] == "loc_lab_3"


def test_lookup_by_location_id(isolated_store):
    result = lookup_campus_location("loc_north_gate")
    assert result["status"] == "ok"
    assert result["location"]["display_name"] == "North Gate"


def test_lookup_unknown_location(isolated_store):
    result = lookup_campus_location("the old building")
    assert result["status"] == "error"
    assert result["error"] == "location_not_found"


def test_lookup_empty_query(isolated_store):
    result = lookup_campus_location("")
    assert result["status"] == "error"
    assert result["error"] == "empty_query"


def test_lookup_non_string_query_does_not_crash(isolated_store):
    for bad in (None, 123, 3.14, True, ["Lab 3"], {"a": "Lab 3"}):
        result = lookup_campus_location(bad)
        assert result["status"] in ("ok", "error")
        assert result["status"] == "error", f"{bad!r} must not resolve"


# --- create_issue ------------------------------------------------------------


@pytest.mark.parametrize("category", CATEGORIES)
def test_create_issue_all_categories(isolated_store, category):
    result = create_issue(category, "A {0} problem near the plaza".format(category), "loc_central_plaza", "MEDIUM")
    assert result["status"] == "ok"
    issue = result["issue"]
    assert issue["category"] == category
    assert re.fullmatch(ISSUE_ID_RE, issue["issue_id"])
    assert issue["status"] == "REPORTED"
    assert issue["location_id"] == "loc_central_plaza"
    assert issue["source_channel"] == "cli"


def test_create_issue_generates_sequential_ids_per_category(isolated_store):
    a = create_issue("WATER", "leak", "loc_lab_3", "LOW")
    b = create_issue("WATER", "leak", "loc_lab_3", "LOW")
    c = create_issue("ENERGY", "light", "loc_lab_4", "LOW")
    assert a["issue_id"] == "WTR-003"
    assert b["issue_id"] == "WTR-004"
    assert c["issue_id"] == "ENE-004"


def test_create_issue_persists_to_disk(isolated_store, isolated_data_dir):
    import json

    create_issue("WASTE", "overflowing bins near the cafe", "loc_student_cafe", "HIGH")
    raw = json.loads((isolated_data_dir / "issues.json").read_text(encoding="utf-8"))
    ids = [item["issue_id"] for item in raw["issues"]]
    assert "WST-003" in ids
    created = next(item for item in raw["issues"] if item["issue_id"] == "WST-003")
    assert created["category"] == "WASTE"
    assert created["status"] == "REPORTED"


def test_create_issue_duplicate_reports_get_distinct_ids(isolated_store):
    first = create_issue("WATER", "There's a water leak outside Lab 3.", "loc_lab_3", "HIGH")
    second = create_issue("WATER", "There's a water leak outside Lab 3.", "loc_lab_3", "HIGH")
    assert first["status"] == "ok" and second["status"] == "ok"
    assert first["issue_id"] != second["issue_id"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"category": "ROCKETS", "description": "x", "location_id": "loc_lab_3", "priority": "LOW"},
        {"category": "WATER", "description": "x", "location_id": "loc_lab_3", "priority": "URGENT"},
        {"category": "WATER", "description": "x", "location_id": "loc_nowhere", "priority": "LOW"},
        {"category": "WATER", "description": "   ", "location_id": "loc_lab_3", "priority": "LOW"},
    ],
)
def test_create_issue_validation_errors(isolated_store, kwargs):
    result = create_issue(**kwargs)
    assert result["status"] == "error"
    assert result["error"] in {
        "invalid_category",
        "invalid_priority",
        "unknown_location",
        "missing_description",
    }


def test_create_issue_invalid_inputs_do_not_crash(isolated_store):
    # A None description becomes empty -> missing_description; any other value
    # coerces to a non-empty string and legitimately creates an issue. Never crash.
    assert create_issue("WATER", None, "loc_lab_3", "LOW")["error"] == "missing_description"
    for bad in (123, 3.14, True, ["leak"], {"a": "leak"}):
        result = create_issue("WATER", bad, "loc_lab_3", "LOW")
        assert result["status"] == "ok", f"description {bad!r} must coerce to text"
    for bad in (None, 123, ["loc_lab_3"]):
        result = create_issue("WATER", "leak", bad, "LOW")
        assert result["status"] == "error", f"bad location_id {bad!r} must not resolve"


def test_create_with_invalid_location_leaves_file_unchanged(isolated_store, isolated_data_dir):
    before = (isolated_data_dir / "issues.json").read_text(encoding="utf-8")
    for bad in (None, 123, ["loc_lab_3"], "loc_nowhere"):
        create_issue("WATER", "leak", bad, "HIGH")
    after = (isolated_data_dir / "issues.json").read_text(encoding="utf-8")
    assert after == before, "failed or coerced-none creates must not persist anything"


def test_create_issue_reported_by_fallback_and_acting_user(isolated_store):
    def without_acting_user():
        return create_issue("WATER", "leak", "loc_lab_3", "LOW")["issue"]["reported_by"]

    def with_acting_user():
        ToolContext.get().session.get_volatile_cache().set("ak.acting_user_id", "+15550000001")
        return create_issue("WATER", "leak", "loc_lab_3", "LOW")["issue"]["reported_by"]

    assert _within_session("session-fallback", without_acting_user) == "student"
    assert _within_session("session-acting", with_acting_user) == "+15550000001"


def test_create_issue_explicit_reporter_and_channel(isolated_store):
    result = create_issue("WATER", "leak", "loc_lab_3", "LOW", reported_by="+15550000001", source_channel="whatsapp")
    assert result["status"] == "ok"
    assert result["issue"]["reported_by"] == "+15550000001"
    assert result["issue"]["source_channel"] == "whatsapp"


# --- get_issue ---------------------------------------------------------------


def test_get_issue_existing(isolated_store):
    result = get_issue("WTR-001")
    assert result["status"] == "ok"
    issue = result["issue"]
    assert issue["issue_id"] == "WTR-001"
    assert issue["category"] == "WATER"
    assert issue["location"] == "Lab 3"
    assert issue["assigned_team"] == "Facilities Zone B"
    assert issue["priority"] == "HIGH"
    assert issue["status"] == "REPORTED"


def test_get_issue_case_insensitive(isolated_store):
    assert get_issue("wtr-001")["status"] == "ok"


def test_get_issue_unknown(isolated_store):
    result = get_issue("WTR-999")
    assert result["status"] == "error"
    assert result["error"] == "issue_not_found"


@pytest.mark.parametrize("bad", ["", "abc", "WTR", "123", "WTR-", "WTR-12", "wtr-1x", None, 12345])
def test_get_issue_malformed_id(isolated_store, bad):
    result = get_issue(bad)
    assert result["status"] == "error"
    assert result["error"] in ("invalid_issue_id", "issue_not_found")


# --- update_issue ------------------------------------------------------------


def test_update_status_and_priority(isolated_store):
    result = update_issue("WTR-001", status="IN_PROGRESS", priority="CRITICAL", additional_note="tech on site")
    assert result["status"] == "ok"
    assert result["issue"]["status"] == "IN_PROGRESS"
    assert result["issue"]["priority"] == "CRITICAL"
    stored = get_issue("WTR-001")["issue"]
    assert stored["status"] == "IN_PROGRESS"
    assert stored["history"][-1]["note"] == "tech on site"


def test_update_persists_history(isolated_store):
    update_issue("WTR-001", status="ASSIGNED", additional_note="handed to zone B")
    stored = get_issue("WTR-001")["issue"]
    assert [entry["event"] for entry in stored["history"]] == ["created", "updated"]
    assert stored["history"][-1]["note"] == "handed to zone B"


def test_update_invalid_status_and_priority(isolated_store):
    assert update_issue("WTR-001", status="NOT_A_STATUS")["error"] == "invalid_status"
    assert update_issue("WTR-001", priority="MAXIMAL")["error"] == "invalid_priority"


def test_update_unknown_issue(isolated_store):
    assert update_issue("WTR-999", status="IN_PROGRESS")["error"] == "issue_not_found"


def test_update_no_changes_rejected(isolated_store):
    assert update_issue("WTR-001")["error"] == "no_changes"


def test_update_invalid_id(isolated_store):
    assert update_issue("bogus", status="IN_PROGRESS")["error"] == "invalid_issue_id"


@pytest.mark.parametrize(
    ("current", "target", "allowed"),
    [
        (status, target, target == status or target in ALLOWED_TRANSITIONS.get(status, set()))
        for status in STATUSES
        for target in STATUSES
    ],
)
def test_lifecycle_state_machine(isolated_store, current, target, allowed):
    issue = get_issue("WTR-001")["issue"]
    isolated_store.issues = [item for item in isolated_store.issues if item["issue_id"] != "WTR-001"]
    isolated_store.issues.append({**issue, "status": current})
    isolated_store.persist()

    result = update_issue("WTR-001", status=target)
    if allowed:
        assert result["status"] == "ok", f"{current} -> {target} must be allowed"
        assert result["issue"]["status"] == target
    else:
        assert result["status"] == "error", f"{current} -> {target} must be rejected"
        assert result["error"] == "invalid_transition"
        assert get_issue("WTR-001")["issue"]["status"] == current, "rejected move must not alter the issue"


def test_closed_is_terminal(isolated_store):
    assert update_issue("WTR-001", status="RESOLVED")["status"] == "ok"
    assert update_issue("WTR-001", status="CLOSED")["status"] == "ok"
    for target in STATUSES:
        if target == "CLOSED":
            continue
        result = update_issue("WTR-001", status=target)
        assert result["status"] == "error"
        assert result["error"] == "invalid_transition"
    assert get_issue("WTR-001")["issue"]["status"] == "CLOSED"


def test_escalated_can_reach_resolved_and_resolve_requires_closure(isolated_store):
    assert (
        update_issue("POL-001", status="RESOLVED", resolution_note="smoke traced to faulty generator")["status"] == "ok"
    )
    result = update_issue("POL-001", status="ESCALATED")
    assert result["error"] == "invalid_transition"


# --- notify_team -------------------------------------------------------------


def test_notify_team_ok(isolated_store):
    result = notify_team("team_facilities_zone_b", "WTR-001", "Water leak reported.", "new_issue")
    assert result["status"] == "ok"
    assert result["notification_id"].startswith("NOT-")
    assert result["delivered"] is True
    assert result["team_id"] == "team_facilities_zone_b"
    matches = [n for n in isolated_store.notifications if n["notification_id"] == result["notification_id"]]
    assert len(matches) == 1
    assert matches[0]["issue_id"] == "WTR-001"
    assert matches[0]["notification_type"] == "new_issue"
    assert matches[0]["delivered"] is True


def test_notify_team_invalid_inputs(isolated_store):
    assert notify_team("team_nowhere", "WTR-001")["error"] == "unknown_team"
    assert notify_team("team_facilities_zone_b", "WTR-999")["error"] == "unknown_issue"
    assert notify_team("team_facilities_zone_b", "bogus")["error"] == "invalid_issue_id"
    assert (
        notify_team("team_facilities_zone_b", "WTR-001", notification_type="carrier_pigeon")["error"]
        == "invalid_notification_type"
    )


def test_notify_failure_records_nothing(isolated_store):
    before = len(isolated_store.notifications)
    notify_team("team_nowhere", "WTR-001")
    assert len(isolated_store.notifications) == before


def test_notify_team_status_for_unknown_ids(isolated_store):
    assert notify_team(None, "WTR-001")["status"] == "error"


# --- get_sustainability_report ------------------------------------------------


def test_seed_report_counts_all_periods(isolated_store):
    expected = {"WATER": 2, "ENERGY": 3, "WASTE": 2, "FOOD": 1, "POLLUTION": 1, "INFRASTRUCTURE": 2, "OTHER": 0}
    for period in ("all", "quarter", "month"):
        report = get_sustainability_report(period=period)
        assert report["status"] == "ok"
        assert report["category_counts"] == expected
        assert report["open_issue_count"] == 11
    week = get_sustainability_report(period="week")
    assert week["status"] == "ok"
    assert all(count == 0 for count in week["category_counts"].values())


def test_seed_report_top_category_and_trends(isolated_store):
    report = get_sustainability_report(period="all")
    assert report["notable_trends"]
    assert any("ENERGY" in trend for trend in report["notable_trends"])
    assert any(trend.startswith("Energy") for trend in report["notable_trends"])


def test_report_filters_by_category_and_location(isolated_store):
    water = get_sustainability_report(period="all", category="WATER")
    assert water["category_counts"]["WATER"] == 2
    assert sum(water["category_counts"].values()) == 2
    lab3 = get_sustainability_report(period="all", location_id="loc_lab_3")
    assert lab3["category_counts"]["WATER"] == 1


def test_report_validation(isolated_store):
    assert get_sustainability_report(period="fortnight")["error"] == "invalid_period"
    assert get_sustainability_report(period="all", category="ROCKETS")["error"] == "invalid_category"
    assert get_sustainability_report(period="all", location_id="loc_nowhere")["error"] == "unknown_location_id"


def test_report_survives_missing_or_malformed_trends(isolated_store, monkeypatch):
    real_load = campus_tool._load_json
    calls = []

    def fake_load(filename):
        if filename == "sustainability.json":
            raise json.JSONDecodeError("boom", "sustainability.json", 0)
        return real_load(filename)

    monkeypatch.setattr(campus_tool, "_load_json", fake_load)
    report = get_sustainability_report(period="all")
    assert report["status"] == "ok"
    assert report["category_counts"]["ENERGY"] == 3
    assert any("leads the recorded issue counts" in trend for trend in report["notable_trends"])


# --- search_issues -----------------------------------------------------------


def test_search_issues_lists_all_and_orders_by_priority_then_recency(isolated_store):
    result = search_issues()
    assert result["status"] == "ok"
    assert result["count"] == 11 and result["total_matches"] == 11
    ids = [item["issue_id"] for item in result["issues"]]
    assert set(ids) == {
        "WTR-001",
        "WTR-002",
        "ENE-001",
        "ENE-002",
        "ENE-003",
        "WST-001",
        "WST-002",
        "FOD-001",
        "POL-001",
        "INF-001",
        "INF-002",
    }
    # Highest priority first; within HIGH, the newer report precedes the older one.
    assert result["issues"][0]["issue_id"] == "ENE-002"
    assert result["issues"][1]["issue_id"] == "POL-001"


def test_search_issues_filters_by_category_status_and_location(isolated_store):
    by_category = search_issues(category="WATER")
    assert by_category["status"] == "ok" and by_category["count"] == 2
    assert {item["issue_id"] for item in by_category["issues"]} == {"WTR-001", "WTR-002"}

    open_all = search_issues(status="OPEN")
    assert open_all["count"] == 11

    solar = search_issues(location_id="loc_solar_array")
    assert {item["issue_id"] for item in solar["issues"]} == {"ENE-002", "INF-001"}
    assert all(item["location"] == "Solar Array" for item in solar["issues"])


def test_search_issues_open_excludes_resolved_and_closed(isolated_store):
    resolved = create_issue("WATER", "already fixed", "loc_lab_3", "LOW")
    update_issue(resolved["issue_id"], status="RESOLVED", resolution_note="done")
    closed = create_issue("ENERGY", "old closed ticket", "loc_lab_4", "LOW")
    update_issue(closed["issue_id"], status="RESOLVED", resolution_note="done")
    update_issue(closed["issue_id"], status="CLOSED")

    opened = search_issues(status="OPEN")
    assert closed["issue_id"] not in {item["issue_id"] for item in opened["issues"]}
    assert resolved["issue_id"] not in {item["issue_id"] for item in opened["issues"]}

    resolved_list = search_issues(status="RESOLVED")
    assert resolved["issue_id"] in {item["issue_id"] for item in resolved_list["issues"]}
    assert closed["issue_id"] not in {item["issue_id"] for item in resolved_list["issues"]}

    closed_list = search_issues(status="CLOSED")
    assert closed["issue_id"] in {item["issue_id"] for item in closed_list["issues"]}


def test_search_issues_filters_are_case_insensitive(isolated_store):
    upper = search_issues(category="WATER", status="OPEN")
    lower = search_issues(category="water", status="open")
    assert upper["status"] == "ok"
    assert upper["issues"] == lower["issues"]


def test_search_issues_limit_caps_results_but_reports_total(isolated_store):
    result = search_issues(limit=3)
    assert result["status"] == "ok"
    assert len(result["issues"]) == 3
    assert result["count"] == 3
    assert result["total_matches"] == 11, "total_matches must reflect all matches, not the capped window"


def test_search_issues_validation_errors(isolated_store):
    assert search_issues(category="CLIMATE")["error"] == "invalid_category"
    assert search_issues(status="BOGUS")["error"] == "invalid_status"
    assert search_issues(location_id="loc_nowhere")["error"] == "unknown_location_id"
    assert search_issues(limit=0)["error"] == "invalid_limit"
    assert search_issues(limit=101)["error"] == "invalid_limit"
    assert search_issues(limit="many")["error"] == "invalid_limit"


def test_search_issues_presenter_excludes_full_history(isolated_store):
    item = search_issues(limit=1)["issues"][0]
    for key in (
        "issue_id",
        "category",
        "description",
        "location",
        "location_id",
        "priority",
        "status",
        "assigned_team",
    ):
        assert key in item
    assert "history" not in item
    assert "reported_by" not in item


def test_search_issues_empty_match_returns_count_zero(isolated_store):
    result = search_issues(category="WATER", status="RESOLVED")
    assert result["status"] == "ok"
    assert result["count"] == 0 and result["total_matches"] == 0
    assert result["issues"] == []


# --- non-string torture across all tools --------------------------------------


@pytest.mark.parametrize("call", ["lookup", "create", "get", "update", "notify", "report", "search"])
def test_non_string_inputs_never_raise(isolated_store, call):
    weird = (None, 123, 3.14, True, ["x"], {"a": 1})
    invocations = []
    label = {
        "lookup": lambda value: lookup_campus_location(value),
        "create": lambda value: create_issue(value, value, value, value),
        "get": lambda value: get_issue(value),
        "update": lambda value: update_issue(value, priority=value, status=value, additional_note=value),
        "notify": lambda value: notify_team(value, value, message=value, notification_type=value),
        "report": lambda value: get_sustainability_report(period=value, category=value, location_id=value),
        "search": lambda value: search_issues(category=value, status=value, location_id=value, limit=value),
    }
    for value in weird:
        result = label[call](value)
        assert isinstance(result, dict) and result.get("status") in ("ok", "error")
        invocations.append(value)
    assert len(invocations) == len(weird)


# --- session active-issue cache ----------------------------------------------


def test_create_issue_stores_active_issue_in_session_cache(isolated_store):
    def run():
        created = create_issue("ENERGY", "lights on overnight", "loc_lab_4", "MEDIUM")
        cache = ToolContext.get().session.get_non_volatile_cache()
        return created, cache.get("active_issue_id")

    created, cached = _within_session("sessionA", run)
    assert created["status"] == "ok"
    assert cached == created["issue_id"]


def test_active_issue_cache_is_isolated_between_sessions(isolated_store):
    def first_session():
        create_issue("ENERGY", "lights on in Lab 4", "loc_lab_4", "MEDIUM")
        return ToolContext.get().session.get_non_volatile_cache().get("active_issue_id")

    first = _within_session("sessionA", first_session)

    def other_session():
        return ToolContext.get().session.get_non_volatile_cache().get("active_issue_id")

    other = _within_session("sessionB", other_session)
    assert first is not None
    assert other is None


# --- no-write-on-failure / retry safety ---------------------------------------


def test_rejected_lifecycle_move_does_not_change_file(isolated_store, isolated_data_dir):
    before = (isolated_data_dir / "issues.json").read_text(encoding="utf-8")
    update_issue("WTR-001", status="CLOSED")  # illegal from REPORTED
    after = (isolated_data_dir / "issues.json").read_text(encoding="utf-8")
    assert after == before


def test_rejected_update_inconsistent_input_does_not_change_file(isolated_store, isolated_data_dir):
    before = (isolated_data_dir / "issues.json").read_text(encoding="utf-8")
    update_issue("WTR-001", status="BOGUS")
    assert (isolated_data_dir / "issues.json").read_text(encoding="utf-8") == before


def test_create_with_invalid_location_leaves_file_unchanged(isolated_store, isolated_data_dir):
    before = (isolated_data_dir / "issues.json").read_text(encoding="utf-8")
    create_issue("WATER", "leak", "loc_nowhere", "HIGH")
    assert (isolated_data_dir / "issues.json").read_text(encoding="utf-8") == before


# --- performance smoke --------------------------------------------------------


def test_many_creates_complete_quickly(isolated_store):
    import time

    start = time.perf_counter()
    for i in range(200):
        result = create_issue("INFRASTRUCTURE", f"broken tile {i}", "loc_gym_hall", "LOW")
        assert result["status"] == "ok"
    elapsed = time.perf_counter() - start
    assert elapsed < 5.0, f"200 creates took {elapsed:.2f}s"
    assert isolated_store.next_issue_id("INFRASTRUCTURE") == "INF-203"
