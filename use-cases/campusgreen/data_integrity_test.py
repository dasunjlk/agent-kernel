"""Data-integrity tests for the committed CampusGreen seed data.

These verify the ``data/`` JSON files are internally consistent so the tools and
the agent can rely on them: every cross-file reference resolves, no IDs are
ambiguous, every status in the seed is reachable from REPORTED through the SPEC
section 13 lifecycle, the category taxonomy is the canonical 7, and the
sustainability trend sentences cover exactly those categories.
"""

from __future__ import annotations

import json
import re
from datetime import datetime

import pytest

from tool import ALLOWED_TRANSITIONS, CATEGORIES, CATEGORY_ID_PREFIXES, PRIORITIES, STATUSES


def _load(isolated_data_dir, name: str):
    return json.loads((isolated_data_dir / name).read_text(encoding="utf-8"))


@pytest.fixture
def locations(isolated_data_dir):
    return _load(isolated_data_dir, "locations.json")


@pytest.fixture
def teams(isolated_data_dir):
    return _load(isolated_data_dir, "teams.json")


@pytest.fixture
def issues(isolated_data_dir):
    return _load(isolated_data_dir, "issues.json")["issues"]


@pytest.fixture
def sustainability(isolated_data_dir):
    return _load(isolated_data_dir, "sustainability.json")


# --- taxonomy ----------------------------------------------------------------


def test_canonical_category_taxonomy():
    expected = {"WATER", "ENERGY", "WASTE", "FOOD", "POLLUTION", "INFRASTRUCTURE", "OTHER"}
    assert set(CATEGORIES) == expected
    assert set(CATEGORIES) == set(CATEGORY_ID_PREFIXES)


def test_branch_flags_exist(isolated_data_dir):
    for name in ("locations.json", "teams.json", "issues.json", "sustainability.json"):
        assert (isolated_data_dir / name).exists(), f"missing data file {name}"


# --- locations ----------------------------------------------------------------


def test_location_structure_and_teams(locations, teams):
    team_ids = {team["team_id"] for team in teams}
    assert len(locations) >= 10, "campus directory must have at least 10 locations"
    for location in locations:
        assert location["location_id"].startswith("loc_")
        assert location["display_name"].strip()
        assert location["zone"]
        assert (
            location["responsible_team_id"] in team_ids
        ), f"{location['location_id']} points at unknown team {location['responsible_team_id']}"


def test_location_ids_are_unique(locations):
    ids = [location["location_id"] for location in locations]
    assert len(ids) == len(set(ids))


# --- teams --------------------------------------------------------------------


def test_teams_have_mock_contact_channel(teams):
    assert len(teams) >= 6
    for team in teams:
        assert team["name"].strip()
        assert team["contact_channel"].startswith(
            "mock://"
        ), f"{team['team_id']} must use a mock:// contact channel in the prototype data"


def test_team_ids_are_unique(teams):
    ids = [team["team_id"] for team in teams]
    assert len(ids) == len(set(ids))


# --- issues -------------------------------------------------------------------


def test_issue_references_resolve(issues, locations, teams):
    location_ids = {location["location_id"] for location in locations}
    team_ids = {team["team_id"] for team in teams}
    for issue in issues:
        assert issue["location_id"] in location_ids, f"{issue['issue_id']} unknown location"
        assert issue["assigned_team_id"] in team_ids, f"{issue['issue_id']} unknown team"


def test_issue_id_prefix_matches_category(issues):
    for issue in issues:
        prefix = CATEGORY_ID_PREFIXES[issue["category"]]
        assert issue["issue_id"].startswith(prefix), f"{issue['issue_id']} prefix != {prefix}"
        assert re.fullmatch(r"[A-Z]{3}-\d{3,}", issue["issue_id"])


def test_issue_ids_are_unique(issues):
    ids = [issue["issue_id"] for issue in issues]
    assert len(ids) == len(set(ids))


def test_issue_statuses_and_priorities_are_valid(issues):
    for issue in issues:
        assert issue["status"] in STATUSES, f"{issue['issue_id']} invalid status {issue['status']}"
        assert issue["priority"] in PRIORITIES, f"{issue['issue_id']} invalid priority {issue['priority']}"


def test_issue_category_coverage(issues):
    non_other = set(CATEGORIES) - {"OTHER"}
    present = {issue["category"] for issue in issues}
    assert non_other <= present, f"seed data lacks issues for {non_other - present}"
    assert all(issue["category"] in CATEGORIES for issue in issues)


def test_issue_history_shape_and_timestamps(issues):
    for issue in issues:
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", issue["created_at"]), issue["issue_id"]
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", issue["updated_at"]), issue["issue_id"]
        assert isinstance(issue["history"], list) and issue["history"]
        for entry in issue["history"]:
            assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", entry["timestamp"]), entry
            assert entry["event"] in {
                "created",
                "assigned",
                "updated",
                "escalated",
                "resolved",
                "closed",
                "status_changed",
            }
            assert isinstance(entry.get("note", ""), str)


# --- lifecycle ----------------------------------------------------------------


def test_every_seeded_status_reachable_from_reported(issues):
    def reachable(target: str) -> bool:
        seen = {"REPORTED"}
        queue = ["REPORTED"]
        while queue:
            current = queue.pop(0)
            if current == target:
                return True
            for nxt in ALLOWED_TRANSITIONS.get(current, set()):
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append(nxt)
        return False

    for issue in issues:
        assert reachable(issue["status"]), f"{issue['issue_id']} status {issue['status']} is unreachable from REPORTED"


def test_no_dead_issue_before_resolved(issues):
    for issue in issues:
        if issue["status"] == "CLOSED":
            continue
        assert ALLOWED_TRANSITIONS.get(issue["status"]) is not None


# --- sustainability trends ----------------------------------------------------


def test_trend_sentences_cover_all_categories(sustainability):
    trends = sustainability.get("trends") or {}
    for category in CATEGORIES:
        assert category in trends, f"sustainability.json has no trend sentence for {category}"
        assert trends[category].strip()


def test_sustainability_schema_has_only_trends(sustainability):
    assert isinstance(sustainability, dict)
    assert set(sustainability.keys()) == {"trends"}
