"""Tests for the CampusGreen agent and tools.

Deterministic unit tests (no API key or network needed) cover agent
construction, instructions, configuration, and every tool in tool.py. Each
tool test runs against an isolated IssueStore on a temp path so the committed
seed data in ``data/issues.json`` is never mutated.

Conversational tests are driven through the Agent Kernel built-in Test harness
(``Test("demo.py")``) following the repository's example pattern, and are
skipped when either precondition is missing:

- ``OPENAI_API_KEY`` is not set (conversational tests call a real LLM).
- the Agent Kernel CLI is unavailable on the platform (``agentkernel.cli``
  imports the Unix-only ``readline`` module, so CLI demos cannot run on stock
  Windows).

Run from this directory::

    uv run pytest -s
"""

import json
import os
import re
import shutil
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio
from agentkernel.core.config import AKConfig
from agentkernel.test import Test
from agentkernel.test.config import AKTestConfig

import tool as campus_tool
from agent import AGENTS, INSTRUCTIONS, campusgreen_agent
from tool import (
    CATEGORIES,
    IssueStore,
    Tools,
    create_issue,
    get_issue,
    get_sustainability_report,
    lookup_campus_location,
    notify_team,
    update_issue,
)

DATA_FILES = ["locations.json", "teams.json", "issues.json", "sustainability.json"]

CATEGORY_LIST = CATEGORIES

requires_openai_key = pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="OPENAI_API_KEY is not set; skipping conversational agent test",
)

try:
    import agentkernel.cli  # noqa: F401

    _CLI_AVAILABLE = True
except ModuleNotFoundError:
    _CLI_AVAILABLE = False

cli_unavailable = pytest.mark.skipif(
    not _CLI_AVAILABLE,
    reason="agentkernel.cli is unavailable on this platform (imports the Unix-only readline module)",
)


def normalized(text: str) -> str:
    """Collapse whitespace so line-wrapped instructions can still be matched phrase-wise."""
    return " ".join(text.split())


def _seed_data_dir(target: Path) -> None:
    source = Path(__file__).resolve().parent / "data"
    target.mkdir(parents=True, exist_ok=True)
    for name in DATA_FILES:
        shutil.copyfile(source / name, target / name)


def _make_issue(
    category: str,
    location_id: str,
    issue_id: str,
    priority: str = "MEDIUM",
    status: str = "REPORTED",
    created_at: str = "2026-08-01T00:00:00Z",
    description: str = "Test issue",
) -> dict:
    return {
        "issue_id": issue_id,
        "category": category,
        "location_id": location_id,
        "description": description,
        "priority": priority,
        "status": status,
        "assigned_team_id": "team_facilities_zone_b",
        "reported_by": "test",
        "source_channel": "cli",
        "created_at": created_at,
        "updated_at": created_at,
        "history": [{"timestamp": created_at, "event": "created", "note": "Created for tests."}],
    }


# --- Fixtures -----------------------------------------------------------------


@pytest.fixture
def issue_store(tmp_path, monkeypatch):
    store = IssueStore(tmp_path / "issues.json")
    monkeypatch.setattr(campus_tool, "_ISSUE_STORE", store)
    return store


@pytest.fixture(scope="session")
def isolated_data_dir() -> Path:
    target = Path(tempfile.mkdtemp(prefix="campusgreen-data-"))
    _seed_data_dir(target)
    os.environ["CAMPUSGREEN_DATA_DIR"] = str(target)
    yield target
    os.environ.pop("CAMPUSGREEN_DATA_DIR", None)


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def test_client(isolated_data_dir):
    test = Test("demo.py")
    await test.start()
    try:
        yield test
    finally:
        await test.stop()


# --- Deterministic unit tests: agent & configuration ---------------------------


def test_agent_module_defines_a_single_agent():
    assert len(AGENTS) == 1
    assert AGENTS[0] is campusgreen_agent


def test_agent_name_is_campusgreen():
    assert campusgreen_agent.name == "campusgreen"


def test_agent_binds_all_six_tools():
    names = {tool.name for tool in campusgreen_agent.tools}
    assert names == {
        "lookup_campus_location",
        "create_issue",
        "get_issue",
        "update_issue",
        "notify_team",
        "get_sustainability_report",
    }
    for tool in campusgreen_agent.tools:
        assert tool.description.strip()


def test_tool_module_exports_the_six_tools():
    assert {func.__name__ for func in Tools} == {
        "lookup_campus_location",
        "create_issue",
        "get_issue",
        "update_issue",
        "notify_team",
        "get_sustainability_report",
    }


def test_agent_instructions_are_configured():
    assert campusgreen_agent.instructions == INSTRUCTIONS
    assert len(normalized(INSTRUCTIONS)) > 100


def test_instructions_cover_all_categories():
    text = normalized(INSTRUCTIONS)
    for category in CATEGORY_LIST:
        assert f"- {category}:" in text, f"category {category} missing from instructions"


def test_instructions_cover_issue_concepts():
    text = normalized(INSTRUCTIONS)
    for concept in ["location", "description", "category", "priority", "issue status", "sustainability impact"]:
        assert concept in text, f"concept {concept!r} missing from instructions"


def test_instructions_describe_each_tool():
    text = normalized(INSTRUCTIONS)
    for tool in [
        "lookup_campus_location",
        "create_issue",
        "get_issue",
        "update_issue",
        "notify_team",
        "get_sustainability_report",
    ]:
        assert tool in text, f"tool {tool!r} missing from instructions"


def test_instructions_forbid_false_external_actions():
    text = normalized(INSTRUCTIONS)
    for forbidden in [
        "Do not invent campus information",
        "Do not invent issue IDs",
        "Do not claim that a ticket was created",
        "Do not claim that a team was notified",
        "Do not fabricate sustainability statistics",
        "Do not pretend that any external action occurred",
        "unless a tool actually created it",
        "unless a tool actually notified it",
        "clearly separate understanding a report from submitting it",
    ]:
        assert forbidden in text, f"boundary rule {forbidden!r} missing from instructions"


def test_config_yaml_loads_in_memory_session():
    config = AKConfig.get()
    assert config.session.type == "in_memory"
    assert config.session.cache.size == 256


def test_test_config_yaml_selects_fallback_mode():
    assert AKTestConfig.get().mode == "fallback"


# --- Deterministic unit tests: lookup_campus_location -------------------------


def test_lookup_known_location():
    result = lookup_campus_location("Lab 3")
    assert result["status"] == "ok"
    location = result["location"]
    assert location["location_id"] == "loc_lab_3"
    assert location["building"] == "Engineering Block"
    assert location["zone"] == "Zone B"
    assert location["responsible_team_id"] == "team_facilities_zone_b"
    assert location["responsible_team"] == "Facilities Zone B"


def test_lookup_known_location_by_alias():
    result = lookup_campus_location("engineering lab 3")
    assert result["status"] == "ok"
    assert result["location"]["location_id"] == "loc_lab_3"


def test_lookup_unknown_location():
    result = lookup_campus_location("near the old building")
    assert result["status"] == "error"
    assert result["error"] == "location_not_found"


def test_lookup_empty_query():
    result = lookup_campus_location("   ")
    assert result["status"] == "error"
    assert result["error"] == "empty_query"


# --- Deterministic unit tests: create_issue ------------------------------------


def test_create_issue(issue_store):
    result = create_issue(
        category="WATER", description="Water leak outside Lab 3", location_id="loc_lab_3", priority="HIGH"
    )
    assert result["status"] == "ok"
    assert re.fullmatch(r"WTR-\d{3,}", result["issue_id"])
    assert result["category"] == "WATER"
    assert result["location"] == "Lab 3"
    assert result["assigned_team_id"] == "team_facilities_zone_b"
    issue = result["issue"]
    assert issue["status"] == "REPORTED"
    assert issue["priority"] == "HIGH"
    assert len(issue["history"]) == 1
    stored = issue_store.get(result["issue_id"])
    assert stored is not None
    assert stored["assigned_team_id"] == "team_facilities_zone_b"


def test_create_issue_validation(issue_store):
    base = dict(category="WATER", description="Water leak outside Lab 3", location_id="loc_lab_3", priority="HIGH")

    unknown_location = create_issue(**{**base, "location_id": "loc_not_real"})
    assert unknown_location["status"] == "error"
    assert unknown_location["error"] == "unknown_location"

    bad_category = create_issue(**{**base, "category": "CLIMATE"})
    assert bad_category["status"] == "error"
    assert bad_category["error"] == "invalid_category"

    bad_priority = create_issue(**{**base, "priority": "urgent"})
    assert bad_priority["status"] == "error"
    assert bad_priority["error"] == "invalid_priority"

    empty_description = create_issue(**{**base, "description": "  "})
    assert empty_description["status"] == "error"
    assert empty_description["error"] == "missing_description"

    assert issue_store.issues == [], "failed creation must not write any record"


def test_create_issue_generated_ids_are_unique_and_sequential(issue_store):
    issue_store.add(_make_issue("WATER", "loc_lab_3", "WTR-005"))
    first = create_issue(category="WATER", description="Leak in Lab 3", location_id="loc_lab_3", priority="MEDIUM")
    second = create_issue(category="WATER", description="Another leak", location_id="loc_lab_3", priority="LOW")
    assert first["status"] == "ok" and first["issue_id"] == "WTR-006"
    assert second["status"] == "ok" and second["issue_id"] == "WTR-007"
    energy = create_issue(category="ENERGY", description="Lights on", location_id="loc_lab_4", priority="MEDIUM")
    assert energy["status"] == "ok" and re.fullmatch(r"ENE-\d{3,}", energy["issue_id"])


# --- Deterministic unit tests: get_issue ---------------------------------------


def test_get_existing_issue(issue_store):
    issue_store.add(_make_issue("WATER", "loc_lab_3", "WTR-901", priority="HIGH", status="ESCALATED"))
    result = get_issue("WTR-901")
    assert result["status"] == "ok"
    issue = result["issue"]
    assert issue["issue_id"] == "WTR-901"
    assert issue["category"] == "WATER"
    assert issue["status"] == "ESCALATED"
    assert issue["location"] == "Lab 3"
    assert issue["assigned_team"] == "Facilities Zone B"


def test_get_issue_is_case_insensitive(issue_store):
    issue_store.add(_make_issue("WATER", "loc_lab_3", "WTR-901"))
    assert get_issue("wtr-901")["issue"]["issue_id"] == "WTR-901"


def test_get_unknown_issue(issue_store):
    result = get_issue("WTR-999")
    assert result["status"] == "error"
    assert result["error"] == "issue_not_found"


@pytest.mark.parametrize("malformed", ["", "WTR", "12345", "WTR-abc", "wtr again"])
def test_get_malformed_issue(issue_store, malformed):
    result = get_issue(malformed)
    assert result["status"] == "error"
    assert result["error"] == "invalid_issue_id"


# --- Deterministic unit tests: update_issue ------------------------------------


def test_update_existing_issue(issue_store):
    issue_store.add(_make_issue("WATER", "loc_lab_3", "WTR-901", priority="HIGH", status="REPORTED"))
    result = update_issue(
        "WTR-901", priority="CRITICAL", status="ESCALATED", additional_note="Leak is spreading across the floor."
    )
    assert result["status"] == "ok"
    assert result["issue"]["priority"] == "CRITICAL"
    assert result["issue"]["status"] == "ESCALATED"
    assert result["updated_at"] >= result["issue"]["created_at"]
    assert result["history_entry"]["event"] == "escalated"
    assert "spreading" in result["history_entry"]["note"]
    assert len(result["issue"]["history"]) == 2
    stored = issue_store.get("WTR-901")
    assert stored["priority"] == "CRITICAL" and stored["status"] == "ESCALATED"


def test_update_resolution_records_event(issue_store):
    issue_store.add(_make_issue("WATER", "loc_lab_3", "WTR-901"))
    result = update_issue("WTR-901", status="RESOLVED", resolution_note="Fixed the leak.")
    assert result["status"] == "ok"
    assert result["issue"]["status"] == "RESOLVED"
    assert result["history_entry"]["event"] == "resolved"
    assert result["history_entry"]["note"] == "Fixed the leak."


def test_update_unknown_issue(issue_store):
    result = update_issue("WTR-999", priority="CRITICAL")
    assert result["status"] == "error"
    assert result["error"] == "issue_not_found"


def test_update_without_changes(issue_store):
    issue_store.add(_make_issue("WATER", "loc_lab_3", "WTR-901"))
    result = update_issue("WTR-901")
    assert result["status"] == "error"
    assert result["error"] == "no_changes"


def test_update_invalid_status(issue_store):
    issue_store.add(_make_issue("WATER", "loc_lab_3", "WTR-901"))
    result = update_issue("WTR-901", status="NOT_A_STATUS")
    assert result["status"] == "error"
    assert result["error"] == "invalid_status"


def test_update_invalid_priority(issue_store):
    issue_store.add(_make_issue("WATER", "loc_lab_3", "WTR-901"))
    result = update_issue("WTR-901", priority="ASAP")
    assert result["status"] == "error"
    assert result["error"] == "invalid_priority"


# --- Deterministic unit tests: notify_team -------------------------------------


def test_notify_team(issue_store):
    issue_store.add(_make_issue("WATER", "loc_lab_3", "WTR-901", priority="HIGH"))
    result = notify_team(team_id="team_facilities_zone_b", issue_id="WTR-901", notification_type="new_issue")
    assert result["status"] == "ok"
    assert result["delivered"] is True
    assert result["notification_id"].startswith("NOT-")
    assert result["team_id"] == "team_facilities_zone_b"
    assert len(issue_store.notifications) == 1
    assert issue_store.notifications[0]["message"] == "WATER issue WTR-901: Test issue"


def test_notify_team_unknown_team(issue_store):
    issue_store.add(_make_issue("WATER", "loc_lab_3", "WTR-901"))
    result = notify_team(team_id="team_does_not_exist", issue_id="WTR-901")
    assert result["status"] == "error"
    assert result["error"] == "unknown_team"


def test_notify_team_unknown_issue(issue_store):
    result = notify_team(team_id="team_facilities_zone_b", issue_id="WTR-999")
    assert result["status"] == "error"
    assert result["error"] == "unknown_issue"


def test_notify_team_persists_to_disk(issue_store):
    issue_store.add(_make_issue("WATER", "loc_lab_3", "WTR-901"))
    notify_team(team_id="team_facilities_zone_b", issue_id="WTR-901")
    reloaded = IssueStore(issue_store.path)
    assert len(reloaded.notifications) == 1
    assert reloaded.notifications[0]["delivered"] is True


# --- Deterministic unit tests: get_sustainability_report -----------------------


def test_sustainability_report_counts(issue_store):
    issue_store.add(_make_issue("WATER", "loc_lab_3", "WTR-901", priority="HIGH", status="REPORTED"))
    issue_store.add(_make_issue("ENERGY", "loc_solar_array", "ENE-901", priority="MEDIUM", status="RESOLVED"))
    result = get_sustainability_report(period="all")
    assert result["status"] == "ok"
    assert result["period"] == "all"
    assert result["category_counts"]["WATER"] == 1
    assert result["category_counts"]["ENERGY"] == 1
    assert result["category_counts"]["OTHER"] == 0
    assert result["priority_counts"]["HIGH"] == 1
    assert result["open_issue_count"] == 1
    assert result["top_locations"][0]["location_id"] == "loc_lab_3"
    assert any("ENERGY" in trend for trend in result["notable_trends"])


def test_sustainability_report_period_filter(issue_store):
    issue_store.add(_make_issue("WATER", "loc_lab_3", "WTR-901", created_at="2020-01-01T00:00:00Z"))
    monthly = get_sustainability_report(period="month")
    assert monthly["category_counts"]["WATER"] == 0
    all_results = get_sustainability_report(period="all")
    assert all_results["category_counts"]["WATER"] == 1


def test_sustainability_report_filters(issue_store):
    issue_store.add(_make_issue("WATER", "loc_lab_3", "WTR-901"))
    issue_store.add(_make_issue("ENERGY", "loc_solar_array", "ENE-901"))
    by_category = get_sustainability_report(period="all", category="water")
    assert by_category["category_counts"]["WATER"] == 1
    assert by_category["category_counts"]["ENERGY"] == 0
    by_location = get_sustainability_report(period="all", location_id="loc_solar_array")
    assert by_location["category_counts"]["ENERGY"] == 1
    assert by_location["category_counts"]["WATER"] == 0


def test_sustainability_report_invalid_period(issue_store):
    result = get_sustainability_report(period="decade")
    assert result["status"] == "error"
    assert result["error"] == "invalid_period"


def test_sustainability_report_invalid_inputs(issue_store):
    bad_category = get_sustainability_report(period="all", category="CLIMATE")
    assert bad_category["status"] == "error" and bad_category["error"] == "invalid_category"
    bad_location = get_sustainability_report(period="all", location_id="loc_nope")
    assert bad_location["status"] == "error" and bad_location["error"] == "unknown_location_id"


# --- Deterministic integration: agent-driven tool chain -------------------------


def test_tool_chain_lookup_create_notify(issue_store):
    lookup = lookup_campus_location("Lab 3")
    assert lookup["status"] == "ok"
    location = lookup["location"]

    created = create_issue(
        category="WATER",
        description="Water leak outside Lab 3",
        location_id=location["location_id"],
        priority="HIGH",
    )
    assert created["status"] == "ok"
    issue_id = created["issue_id"]
    assert re.fullmatch(r"WTR-\d{3,}", issue_id)

    notified = notify_team(team_id=location["responsible_team_id"], issue_id=issue_id)
    assert notified["status"] == "ok"
    assert notified["delivered"] is True

    stored = issue_store.get(issue_id)
    assert stored is not None
    assert stored["status"] == "REPORTED"
    assert stored["assigned_team_id"] == "team_facilities_zone_b"
    assert any(record["issue_id"] == issue_id for record in issue_store.notifications)


# --- Conversational harness -----------------------------------------------------


@cli_unavailable
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.order(1)
async def test_local_demo_starts():
    """The local demo boots to the CLI prompt without needing an API key."""
    test = Test("demo.py")
    await test.start()
    try:
        assert test.proc.returncode is None, "demo process exited during startup"
    finally:
        await test.stop()


@requires_openai_key
@cli_unavailable
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.order(2)
async def test_water_leak_report(test_client):
    await test_client.send("There's a water leak outside Lab 3.")
    await test_client.expect(
        [
            "Your water leak has been reported and a ticket has been created. The responsible team has been notified.",
            "I created an issue for the water leak outside Lab 3 and notified Facilities Zone B.",
            "Water leak reported. The issue was created and the responsible team has been notified.",
            "I have recorded the water leak and the responsible facilities team has been notified.",
        ]
    )


@requires_openai_key
@cli_unavailable
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.order(3)
async def test_energy_waste_report(test_client):
    await test_client.send("The lights in Lab 4 have been left on overnight.")
    await test_client.expect(
        [
            "The lights in Lab 4 have been reported as an energy issue and a ticket was created.",
            "I created an energy issue for the lights left on in Lab 4 and notified the responsible team.",
            "Energy waste reported. A ticket has been created and the responsible team has been notified.",
        ]
    )


@requires_openai_key
@cli_unavailable
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.order(4)
async def test_waste_report(test_client):
    await test_client.send("The bins near the Student Cafe are overflowing.")
    await test_client.expect(
        [
            "The overflowing bins near the Student Cafe have been reported as a waste issue.",
            "I created a waste issue for the overflowing bins and notified the responsible team.",
            "Waste issue recorded. A ticket has been created and the team has been notified.",
        ]
    )


@requires_openai_key
@cli_unavailable
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.order(5)
async def test_capabilities_question(test_client):
    await test_client.send("What can you help me with?")
    await test_client.expect(
        [
            "I can help you report campus sustainability issues.",
            "I help coordinate campus sustainability issues ranging from water and energy to waste and pollution.",
            "You can report campus sustainability problems such as water, energy, waste, or pollution.",
        ]
    )


@requires_openai_key
@cli_unavailable
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.order(6)
async def test_unknown_issue_status_not_fabricated(test_client):
    response = await test_client.send("What's the status of ZZZ-999?")
    lowered = response.lower()
    assert "zzz-999" in lowered, f"response should reference the queried ID; got: {response!r}"
    assert (
        "could not find" in lowered or "not found" in lowered or "no issue" in lowered
    ), f"agent must report the lookup failure instead of fabricating status; got: {response!r}"
    assert not re.search(
        r"(status is|currently|now)\s+(reported|in progress|assigned|resolved|closed|escalated)", lowered
    ), f"agent must not invent a lifecycle status; got: {response!r}"


@requires_openai_key
@cli_unavailable
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.order(7)
async def test_missing_information_asks_for_clarification(test_client):
    response = await test_client.send("There is a problem.")
    assert not re.search(
        r"WTR-|has been created", response.lower()
    ), f"agent must not create a ticket; got: {response!r}"
    await test_client.expect(
        [
            "I can help report it. What type of problem are you seeing, and where is it located?",
            "What type of problem are you seeing and where is it located?",
            "Could you describe the issue and tell me where it is happening?",
        ]
    )


@requires_openai_key
@cli_unavailable
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.order(8)
async def test_unknown_location_asks_for_clarification(test_client):
    response = await test_client.send("There's a leak near the old building.")
    assert not re.search(
        r"WTR-|has been created", response.lower()
    ), f"agent must not create a ticket; got: {response!r}"
    await test_client.expect(
        [
            "I could not identify that campus location.",
            "Could you provide the building name, room number, or a nearby known landmark?",
            "That location is not in the campus directory. Can you name a known building or room nearby?",
        ]
    )


@requires_openai_key
@cli_unavailable
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.order(9)
async def test_status_request(test_client):
    await test_client.send("What's the status of WTR-001?")
    await test_client.expect(
        [
            "WTR-001 is currently reported with priority HIGH, assigned to Facilities Zone B.",
            "WTR-001 status is REPORTED with priority HIGH.",
            "WTR-001 is in a REPORTED state and has not been worked on yet.",
        ]
    )


@requires_openai_key
@cli_unavailable
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.order(10)
async def test_sustainability_report(test_client):
    await test_client.send("What are the biggest sustainability problems this month?")
    await test_client.expect(
        [
            "Based on recorded issues this month, ENERGY leads the reported problems on campus.",
            "The biggest sustainability problem this month is energy-related, based on recorded issue counts.",
            "Energy reports are the most common sustainability issue this month according to the data.",
        ]
    )


@requires_openai_key
@cli_unavailable
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.order(11)
async def test_tool_chaining_integration(isolated_data_dir):
    """End-to-end: a report flows lookup -> create_issue -> notify_team in the live CLI.

    Verifies the issue really exists afterward, the ID is valid, a notification
    was actually recorded, and the user-facing response reflects the tool work.
    """
    before = json.loads((isolated_data_dir / "issues.json").read_text(encoding="utf-8"))
    seed_ids = {item["issue_id"] for item in before["issues"]}

    test = Test("demo.py")
    await test.start()
    try:
        response = await test.send("There's a water leak outside Lab 3.")
    finally:
        await test.stop()

    lowered = response.lower()
    assert "water" in lowered, f"response should reflect the reported issue; got: {response!r}"
    assert "created" in lowered or "reported" in lowered, f"response should acknowledge creation; got: {response!r}"
    assert "notif" in lowered, f"response should mention team notification; got: {response!r}"

    after = json.loads((isolated_data_dir / "issues.json").read_text(encoding="utf-8"))
    new_ids = [item["issue_id"] for item in after["issues"] if item["issue_id"] not in seed_ids]
    assert len(new_ids) >= 1, "expected at least one new issue to be persisted"
    for issue_id in new_ids:
        assert re.fullmatch(r"[A-Z]{3}-\d{3,}", issue_id), f"ticket IDs must be tool-generated; got {issue_id!r}"

    created = [
        item
        for item in after["issues"]
        if item["issue_id"] in new_ids and item["category"] == "WATER" and item["location_id"] == "loc_lab_3"
    ]
    assert created, "expected a WATER issue at Lab 3 to be created"
    assert created[0]["status"] in ("REPORTED", "ASSIGNED", "IN_PROGRESS")

    new_notifications = [record for record in after["notifications"] if record["issue_id"] in new_ids]
    assert new_notifications, "expected a notification to be recorded for the new issue"
    assert new_notifications[0]["delivered"] is True
