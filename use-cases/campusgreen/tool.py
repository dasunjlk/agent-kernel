"""CampusGreen tools and the local prototype data layer.

Tools are plain Python functions bound to the CampusGreen agent by
``agentkernel.openai.OpenAIToolBuilder`` (see agent.py). Every tool returns a
dictionary with an envelope: ``{"status": "ok", ...}`` on success and
``{"status": "error", "error": "<code>", "message": "..."}`` on failure, so the
agent can distinguish a real success from a failure and never claim an action
that did not happen.

The deterministic logic (location resolution, validation, issue-ID generation,
notification recording, report computation) lives here rather than in the agent
prompt. The LLM only decides which tools to call and interprets their results.

Data files live under ``data/``. Set the environment variable
``CAMPUSGREEN_DATA_DIR`` to point the tools at a different directory (used by
tests to isolate runs from the committed seed data).
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any

from agentkernel.core import ToolContext

logger = logging.getLogger("ak.campusgreen.tools")

CATEGORIES = ["WATER", "ENERGY", "WASTE", "FOOD", "POLLUTION", "INFRASTRUCTURE", "OTHER"]

PRIORITIES = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]

# Deterministic ordering for issue listings: most severe first, then newest.
# This is a sort order only — a documented, transparent ordering — not a scoring
# or prioritization model. The agent decides what a list means.
PRIORITY_RANK = {priority: index for index, priority in enumerate(PRIORITIES)}

STATUSES = ["REPORTED", "ASSIGNED", "IN_PROGRESS", "ESCALATED", "RESOLVED", "CLOSED"]

# Minimum lifecycle validation required by SPEC.md section 13: issues move
# forward through the diagram REPORTED -> ASSIGNED -> IN_PROGRESS -> ESCALATED
# -> RESOLVED -> CLOSED, escalation is allowed from any still-active status,
# RESOLVED only closes, and CLOSED is terminal. Transitions outside this set
# are rejected with invalid_transition. Identical-status updates are no-ops
# and are not affected.
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "REPORTED": {"ASSIGNED", "IN_PROGRESS", "ESCALATED", "RESOLVED"},
    "ASSIGNED": {"IN_PROGRESS", "ESCALATED", "RESOLVED"},
    "IN_PROGRESS": {"ESCALATED", "RESOLVED"},
    "ESCALATED": {"IN_PROGRESS", "RESOLVED"},
    "RESOLVED": {"CLOSED"},
    "CLOSED": set(),
}

PERIODS = ["week", "month", "quarter", "all"]

NOTIFICATION_TYPES = ["new_issue", "update", "escalation", "status_update", "other"]

CATEGORY_ID_PREFIXES = {
    "WATER": "WTR",
    "ENERGY": "ENE",
    "WASTE": "WST",
    "FOOD": "FOD",
    "POLLUTION": "POL",
    "INFRASTRUCTURE": "INF",
    "OTHER": "OTH",
}

ISSUE_ID_RE = re.compile(r"^[A-Z]{3}-\d{3,}$")

_ISSUE_STORE: "IssueStore | None" = None


class IssueStore:
    """Read/write access to the local issues data file (``issues.json``)."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path: Path = Path(path) if path else _data_dir() / "issues.json"
        self.issues: list[dict[str, Any]] = []
        self.notifications: list[dict[str, Any]] = []
        self._reload()

    def _reload(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self.issues = []
            self.notifications = []
            return
        self.issues = [dict(item) for item in raw.get("issues") or []]
        self.notifications = [dict(item) for item in raw.get("notifications") or []]

    def persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"issues": self.issues, "notifications": self.notifications}
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def all(self) -> list[dict[str, Any]]:
        return [dict(issue) for issue in self.issues]

    def get(self, issue_id: str) -> dict[str, Any] | None:
        needle = (issue_id or "").strip().upper()
        for issue in self.issues:
            if str(issue.get("issue_id", "")).upper() == needle:
                return dict(issue)
        return None

    def next_issue_id(self, category: str) -> str:
        prefix = CATEGORY_ID_PREFIXES[category]
        highest = 0
        for issue in self.issues:
            match = re.fullmatch(re.escape(prefix) + r"-(\d+)", str(issue.get("issue_id", "")))
            if match:
                highest = max(highest, int(match.group(1)))
        return f"{prefix}-{highest + 1:03d}"

    def add(self, issue: dict[str, Any]) -> str:
        self.issues.append(dict(issue))
        self.persist()
        return issue["issue_id"]

    def update(
        self,
        issue_id: str,
        *,
        priority: str | None = None,
        status: str | None = None,
        additional_note: str | None = None,
        resolution_note: str | None = None,
    ) -> dict[str, Any] | None:
        issue = self.get(issue_id)
        if issue is None:
            return None
        now = _utcnow()
        changes = []
        if priority is not None and priority != issue.get("priority"):
            changes.append(f"priority: {issue.get('priority')} -> {priority}")
        if status is not None and status != issue.get("status"):
            changes.append(f"status: {issue.get('status')} -> {status}")
        if priority is not None:
            issue["priority"] = priority
        if status is not None:
            issue["status"] = status
        note = (additional_note or resolution_note or "").strip()
        history_note = note if note else ("; ".join(changes) if changes else "Issue updated.")
        if status == "ESCALATED":
            event = "escalated"
        elif status == "RESOLVED":
            event = "resolved"
        elif status == "CLOSED":
            event = "closed"
        else:
            event = "updated"
        history_entry = {"timestamp": now, "event": event, "note": history_note}
        issue.setdefault("history", []).append(dict(history_entry))
        issue["updated_at"] = now
        self._replace(issue)
        self.persist()
        return issue

    def record_notification(self, record: dict[str, Any]) -> None:
        self.notifications.append(dict(record))
        self.persist()

    def _replace(self, updated: dict[str, Any]) -> None:
        needle = str(updated.get("issue_id", "")).upper()
        for index, issue in enumerate(self.issues):
            if str(issue.get("issue_id", "")).upper() == needle:
                self.issues[index] = dict(updated)
                return


def _data_dir() -> Path:
    override = os.environ.get("CAMPUSGREEN_DATA_DIR")
    if override:
        return Path(override)
    return Path(__file__).resolve().parent / "data"


def _load_json(filename: str) -> Any:
    path = _data_dir() / filename
    return json.loads(path.read_text(encoding="utf-8"))


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _error(error: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"status": "error", "error": error, "message": message, **extra}


def _coerce_str(value: Any) -> str:
    """Return a plain string for any tool argument without crashing.

    Tool inputs come from an LLM and are normally strings, but defensive
    validation matters: a non-string value must produce a structured error
    result, never an AttributeError or a corrupted record.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _logged(func: Any) -> Any:
    """Emit a one-line, non-sensitive trace for every tool call.

    The line records the tool name, the envelope outcome, and a few safe
    identifiers (issue ID, team ID, location display name, error code). It
    never logs credentials, API keys, webhook secrets, or free-text user
    content such as issue descriptions.
    """

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            result = func(*args, **kwargs)
        except Exception as exc:  # observed by the framework; log the type only
            try:
                logger.error("tool=%s status=error exception=%s", func.__name__, type(exc).__name__)
            except Exception:
                pass
            raise
        try:
            if isinstance(result, dict):
                status = str(result.get("status", "unknown"))
                parts = [f"tool={func.__name__}", f"status={status}"]
                if status == "ok":
                    issue = result.get("issue")
                    issue_id = result.get("issue_id") or (issue.get("issue_id") if isinstance(issue, dict) else None)
                    if issue_id:
                        parts.append(f"issue_id={issue_id}")
                    if result.get("team_id"):
                        parts.append(f"team={result['team_id']}")
                    if result.get("notification_type"):
                        parts.append(f"notification_type={result['notification_type']}")
                    if isinstance(result.get("location"), str):
                        parts.append(f"location={result['location']}")
                    if result.get("period"):
                        parts.append(f"period={result['period']}")
                    if result.get("total_matches") is not None:
                        parts.append(f"count={result['total_matches']}")
                elif result.get("error"):
                    parts.append(f"error={result['error']}")
                logger.info(" ".join(parts))
        except Exception:
            pass
        return result

    return wrapper


def _location_by_id(location_id: str) -> dict[str, Any] | None:
    needle = (location_id or "").strip().lower()
    for location in _load_json("locations.json"):
        if str(location.get("location_id", "")).lower() == needle:
            return dict(location)
    return None


def _team_by_id(team_id: str) -> dict[str, Any] | None:
    needle = (team_id or "").strip().lower()
    for team in _load_json("teams.json"):
        if str(team.get("team_id", "")).lower() == needle:
            return dict(team)
    return None


def _issue_store() -> IssueStore:
    global _ISSUE_STORE
    if _ISSUE_STORE is None:
        _ISSUE_STORE = IssueStore()
    return _ISSUE_STORE


def _remember_active_issue(issue_id: str) -> None:
    try:
        session = ToolContext.get().session
        session.get_non_volatile_cache().set("active_issue_id", issue_id)
    except (AttributeError, RuntimeError):
        pass


def _current_session():
    """Return the active session inside a tool call, or None outside one."""
    try:
        return ToolContext.get().session
    except (AttributeError, RuntimeError):
        return None


def _acting_user() -> str | None:
    """Return the end user the current request acts on behalf of, if any.

    Agent Kernel publishes the request's ``user_id`` into the session's
    volatile cache under ``ak.acting_user_id`` when a channel such as WhatsApp
    provides it, so issue records can be attributed to the real reporter
    without the agent having to guess.
    """
    session = _current_session()
    if session is None:
        return None
    try:
        value = session.get_volatile_cache().get("ak.acting_user_id")
    except (AttributeError, RuntimeError):
        return None
    return str(value) if value else None


def _channel() -> str:
    """Channel label recorded on new issues.

    Defaults to ``cli`` for the local demo; set ``CAMPUSGREEN_CHANNEL`` to
    label issues from other surfaces (e.g. ``whatsapp``) without hard-coding a
    channel name into the agent or tool arguments.
    """
    return os.environ.get("CAMPUSGREEN_CHANNEL", "cli")


def _validate_issue_id(raw: Any) -> str | None:
    value = _coerce_str(raw).strip()
    if not value:
        return None
    return value.upper() if re.fullmatch(ISSUE_ID_RE, value.upper()) else None


def _present_issue(issue: dict[str, Any]) -> dict[str, Any]:
    location = _location_by_id(issue.get("location_id", ""))
    team = _team_by_id(issue.get("assigned_team_id", ""))
    return {
        "issue_id": issue["issue_id"],
        "category": issue.get("category", ""),
        "location": issue.get("location_display_name")
        or (location["display_name"] if location else issue.get("location_id", "")),
        "location_id": issue.get("location_id", ""),
        "description": issue.get("description", ""),
        "priority": issue.get("priority", ""),
        "status": issue.get("status", ""),
        "assigned_team": team["name"] if team else issue.get("assigned_team_id", ""),
        "assigned_team_id": issue.get("assigned_team_id", ""),
        "reported_by": issue.get("reported_by", ""),
        "source_channel": issue.get("source_channel", ""),
        "created_at": issue.get("created_at", ""),
        "updated_at": issue.get("updated_at", ""),
        "history": [dict(entry) for entry in issue.get("history") or []],
    }


def _present_search_issue(issue: dict[str, Any]) -> dict[str, Any]:
    """Compact listing form for ``search_issues`` (no full history).

    Keeps list payloads token-light while still carrying everything the agent
    needs to reason about priorities: real ticket ID, category, description,
    location, priority, status, and assigned team.
    """
    location = _location_by_id(issue.get("location_id", ""))
    team = _team_by_id(issue.get("assigned_team_id", ""))
    return {
        "issue_id": issue["issue_id"],
        "category": issue.get("category", ""),
        "description": issue.get("description", ""),
        "location": issue.get("location_display_name")
        or (location["display_name"] if location else issue.get("location_id", "")),
        "location_id": issue.get("location_id", ""),
        "priority": issue.get("priority", ""),
        "status": issue.get("status", ""),
        "assigned_team": team["name"] if team else issue.get("assigned_team_id", ""),
        "created_at": issue.get("created_at", ""),
        "updated_at": issue.get("updated_at", ""),
    }


# --- Tools ------------------------------------------------------------------


@_logged
def lookup_campus_location(query: str) -> dict:
    """Resolve a place name the user mentioned to a known campus location.

    Call this before creating an issue so the location is verified. Matches the
    display name, the location ID, and stored aliases; case-insensitive.
    Returns a success envelope with the location (building, floor, zone,
    responsible team) or a location_not_found error. Never invent a location
    when this tool says a place is unknown; ask the user instead.
    """
    if not isinstance(query, str):
        return _error("location_not_found", f"Query must be a string; got {type(query).__name__}.")
    text = query.strip()
    if not text:
        return _error("empty_query", "No location text was provided.")
    needle = text.lower()
    locations = _load_json("locations.json")

    # Pass 1: exact match against display_name, location_id, or aliases
    for location in locations:
        candidates = [str(location.get("display_name", "")), str(location.get("location_id", ""))]
        candidates.extend(str(alias) for alias in location.get("aliases") or [])
        if needle in {candidate.strip().lower() for candidate in candidates if candidate.strip()}:
            team = _team_by_id(location.get("responsible_team_id", ""))
            return {
                "status": "ok",
                "location": {
                    "location_id": location["location_id"],
                    "display_name": location["display_name"],
                    "building": location.get("building", ""),
                    "floor": location.get("floor", ""),
                    "zone": location.get("zone", ""),
                    "aliases": location.get("aliases") or [],
                    "responsible_team_id": location.get("responsible_team_id", ""),
                    "responsible_team": team["name"] if team else location.get("responsible_team_id", ""),
                },
            }

    # Pass 2: whole-word phrase match (e.g., "outside Lab 3" or "in Main Library restroom")
    best_match = None
    best_len = 0
    for location in locations:
        candidates = [str(location.get("display_name", "")), str(location.get("location_id", ""))]
        candidates.extend(str(alias) for alias in location.get("aliases") or [])
        for cand in candidates:
            cand_clean = cand.strip().lower()
            if not cand_clean or len(cand_clean) < 3:
                continue
            pattern = r"\b" + re.escape(cand_clean) + r"\b"
            if re.search(pattern, needle):
                if len(cand_clean) > best_len:
                    best_len = len(cand_clean)
                    best_match = location

    if best_match is not None:
        team = _team_by_id(best_match.get("responsible_team_id", ""))
        return {
            "status": "ok",
            "location": {
                "location_id": best_match["location_id"],
                "display_name": best_match["display_name"],
                "building": best_match.get("building", ""),
                "floor": best_match.get("floor", ""),
                "zone": best_match.get("zone", ""),
                "aliases": best_match.get("aliases") or [],
                "responsible_team_id": best_match.get("responsible_team_id", ""),
                "responsible_team": team["name"] if team else best_match.get("responsible_team_id", ""),
            },
        }

    return _error("location_not_found", f"No known campus location matches '{text}'.")


@_logged
def create_issue(
    category: str,
    description: str,
    location_id: str,
    priority: str,
) -> dict:
    """Create a new sustainability issue record.

    Requires a valid category, a known location_id (resolve it first with
    lookup_campus_location), a non-empty description, and a valid priority.
    The tool generates the issue ID (e.g. WTR-001) itself. Returns a success
    envelope with the created issue (lifecycle status under ``issue["status"]``)
    or an error. Only claim a ticket was created when this returns ok.
    """
    normalized_category = _coerce_str(category).strip().upper()
    if normalized_category not in CATEGORIES:
        return _error(
            "invalid_category",
            f"Unknown category '{category}'. Allowed categories: {', '.join(CATEGORIES)}.",
        )
    normalized_priority = _coerce_str(priority).strip().upper()
    if normalized_priority not in PRIORITIES:
        return _error(
            "invalid_priority", f"Unknown priority '{priority}'. Allowed priorities: {', '.join(PRIORITIES)}."
        )
    location = _location_by_id(_coerce_str(location_id))
    if location is None:
        return _error(
            "unknown_location",
            f"No known campus location matches location_id '{location_id}'. Resolve the location first with lookup_campus_location.",
        )
    text = _coerce_str(description).strip()
    if not text:
        return _error("missing_description", "A non-empty issue description is required.")

    reporter = _acting_user() or "student"
    channel = _channel()

    store = _issue_store()
    now = _utcnow()
    issue_id = store.next_issue_id(normalized_category)
    issue = {
        "issue_id": issue_id,
        "category": normalized_category,
        "location_id": location["location_id"],
        "location_display_name": location["display_name"],
        "description": text,
        "priority": normalized_priority,
        "status": "REPORTED",
        "assigned_team_id": location["responsible_team_id"],
        "reported_by": reporter,
        "source_channel": channel,
        "created_at": now,
        "updated_at": now,
        "history": [{"timestamp": now, "event": "created", "note": "Initial report created from the user's message."}],
    }
    store.add(issue)
    _remember_active_issue(issue_id)
    return {
        "status": "ok",
        "issue": _present_issue(issue),
        "issue_id": issue_id,
        "category": normalized_category,
        "location": location["display_name"],
        "priority": normalized_priority,
        "assigned_team_id": location["responsible_team_id"],
        "created_at": now,
    }


@_logged
def get_issue(issue_id: str) -> dict:
    """Retrieve a stored issue by its ID (e.g. WTR-001).

    Use to answer status questions or before updating an issue. Returns a
    success envelope with the stored issue (location, assigned team, history)
    or an error for a malformed or unknown ID. Never describe an issue's state
    unless this tool returned it.
    """
    normalized = _validate_issue_id(issue_id)
    if normalized is None:
        return _error("invalid_issue_id", f"'{issue_id}' is not a valid issue ID (expected e.g. WTR-001).")
    issue = _issue_store().get(normalized)
    if issue is None:
        return _error("issue_not_found", f"No issue with ID '{normalized}' exists.")
    return {"status": "ok", "issue": _present_issue(issue)}


def _created_timestamp(issue: dict[str, Any]) -> float:
    """Sortable timestamp for an issue's created_at (malformed -> 0)."""
    raw = str(issue.get("created_at", "")).strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return 0
    return parsed.timestamp()


@_logged
def search_issues(category: str = "", status: str = "", location_id: str = "", limit: int = 20) -> dict:
    """List recorded issues matching optional filters (no ranking opinion).

    Use when you need to inspect the actual issue records behind a question:
    which tickets exist for a category, which are still open, or which belong
    to a location. Unlike get_sustainability_report (aggregate counts), this
    returns the individual issue records with real ticket IDs, status,
    priority, location, and assigned team. Results are ordered by severity
    then recency (CRITICAL > HIGH > MEDIUM > LOW, newest first). status
    "OPEN" means not RESOLVED or CLOSED; any other value must be an exact
    lifecycle status. Returns a success envelope with count, total_matches,
    and the issues list, or an error for invalid filters.

    IMPORTANT: You MUST provide all parameters in the JSON call. If you are not filtering
    by category, status, or location_id, pass an empty string "" for those parameters.
    """
    lowered_category = None
    if _coerce_str(category).strip():
        normalized_category = _coerce_str(category).strip().upper()
        if normalized_category not in CATEGORIES:
            return _error(
                "invalid_category", f"Unknown category '{category}'. Allowed categories: {', '.join(CATEGORIES)}."
            )
        lowered_category = normalized_category

    lowered_status = None
    if _coerce_str(status).strip():
        normalized_status = _coerce_str(status).strip().upper()
        if normalized_status == "OPEN":
            lowered_status = "OPEN"
        elif normalized_status in STATUSES:
            lowered_status = normalized_status
        else:
            return _error(
                "invalid_status",
                f"Unknown status '{status}'. Use OPEN, or one of: {', '.join(STATUSES)}.",
            )

    lowered_location = None
    if _coerce_str(location_id).strip():
        location = _location_by_id(_coerce_str(location_id))
        if location is None:
            return _error("unknown_location_id", f"No campus location matches location_id '{location_id}'.")
        lowered_location = location["location_id"].lower()

    raw_limit = _coerce_str(limit).strip() or "20"
    try:
        parsed_limit = int(raw_limit)
    except ValueError:
        return _error("invalid_limit", "limit must be an integer between 1 and 100.")
    if parsed_limit < 1 or parsed_limit > 100:
        return _error("invalid_limit", "limit must be an integer between 1 and 100.")

    matches = []
    for issue in _issue_store().all():
        if lowered_category is not None and issue.get("category") != lowered_category:
            continue
        if lowered_status is not None:
            issue_status = str(issue.get("status", "")).upper()
            if lowered_status == "OPEN":
                if issue_status in ("RESOLVED", "CLOSED"):
                    continue
            elif issue_status != lowered_status:
                continue
        if lowered_location is not None and str(issue.get("location_id", "")).lower() != lowered_location:
            continue
        matches.append(dict(issue))

    matches.sort(
        key=lambda item: (
            -PRIORITY_RANK.get(str(item.get("priority", "")).upper(), 0),
            -_created_timestamp(item),
            str(item.get("issue_id", "")),
        )
    )
    total = len(matches)
    selected = [dict(item) for item in matches[:parsed_limit]]
    return {
        "status": "ok",
        "count": len(selected),
        "total_matches": total,
        "issues": [_present_search_issue(item) for item in selected],
    }


@_logged
def update_issue(
    issue_id: str,
    priority: str = "",
    status: str = "",
    additional_note: str = "",
    resolution_note: str = "",
) -> dict:
    """Update an existing issue (priority, status, or notes).

    Use for follow-ups such as escalation, progress, resolution, or added
    detail. ``issue_id`` is required and the issue must exist; provide at least
    one field to change. Status must be a lifecycle value and priority one of
    LOW/MEDIUM/HIGH/CRITICAL. Lifecycle moves must follow the SPEC section 13
    state diagram (for example CLOSED is terminal); invalid moves are rejected
    with an invalid_transition error. Returns the updated issue state only when
    the update succeeded; never claim an update or escalation without this ok.

    IMPORTANT: You MUST provide all parameters in the JSON call. If you are not updating
    priority, status, additional_note, or resolution_note, pass an empty string "" for them.
    """
    normalized = _validate_issue_id(issue_id)
    if normalized is None:
        return _error("invalid_issue_id", f"'{issue_id}' is not a valid issue ID (expected e.g. WTR-001).")

    normalized_priority = None
    if priority is not None and _coerce_str(priority).strip():
        normalized_priority = _coerce_str(priority).strip().upper()
        if normalized_priority not in PRIORITIES:
            return _error(
                "invalid_priority", f"Unknown priority '{priority}'. Allowed priorities: {', '.join(PRIORITIES)}."
            )

    normalized_status = None
    if status is not None and _coerce_str(status).strip():
        normalized_status = _coerce_str(status).strip().upper()
        if normalized_status not in STATUSES:
            return _error("invalid_status", f"Unknown status '{status}'. Allowed statuses: {', '.join(STATUSES)}.")

    if (
        normalized_priority is None
        and normalized_status is None
        and not _coerce_str(additional_note).strip()
        and not _coerce_str(resolution_note).strip()
    ):
        return _error(
            "no_changes", "Provide at least one field to update: priority, status, additional_note, or resolution_note."
        )

    current = _issue_store().get(normalized)
    if current is None:
        return _error("issue_not_found", f"No issue with ID '{normalized}' exists.")

    current_status = str(current.get("status", "")).upper()
    if (
        normalized_status is not None
        and normalized_status != current_status
        and normalized_status not in ALLOWED_TRANSITIONS.get(current_status, set())
    ):
        allowed = sorted(ALLOWED_TRANSITIONS.get(current_status, set()))
        return _error(
            "invalid_transition",
            f"Cannot move issue from status '{current_status}' to '{normalized_status}'. "
            f"Allowed moves: {', '.join(allowed) if allowed else 'none (terminal status)'}.",
        )

    updated = _issue_store().update(
        normalized,
        priority=normalized_priority,
        status=normalized_status,
        additional_note=additional_note,
        resolution_note=resolution_note,
    )
    if updated is None:
        return _error("issue_not_found", f"No issue with ID '{normalized}' exists.")

    presented = _present_issue(updated)
    return {
        "status": "ok",
        "issue": presented,
        "issue_id": updated["issue_id"],
        "category": updated["category"],
        "location": presented["location"],
        "priority": updated["priority"],
        "assigned_team": presented["assigned_team"],
        "updated_at": updated["updated_at"],
        "history_entry": updated["history"][-1],
    }


@_logged
def notify_team(
    team_id: str,
    issue_id: str,
    message: str = "",
    notification_type: str = "update",
) -> dict:
    """Notify a campus team about an issue via the local mock channel.

    Use after an issue is created or updated so the responsible team is told.
    Requires a known team ID and an existing issue ID. Returns a success
    envelope with a notification_id and delivered=True only when delivery was
    recorded. Never claim a team was notified without this ok result.

    IMPORTANT: You MUST provide all parameters in the JSON call. Always include 
    'message' (pass "" if none) and 'notification_type' (pass "update" if none).
    """
    team = _team_by_id(_coerce_str(team_id))
    if team is None:
        return _error("unknown_team", f"No campus team matches team_id '{team_id}'.")
    normalized = _validate_issue_id(issue_id)
    if normalized is None:
        return _error("invalid_issue_id", f"'{issue_id}' is not a valid issue ID (expected e.g. WTR-001).")
    issue = _issue_store().get(normalized)
    if issue is None:
        return _error("unknown_issue", f"No issue with ID '{normalized}' exists.")
    ntype = _coerce_str(notification_type).strip() or "update"
    if ntype not in NOTIFICATION_TYPES:
        return _error(
            "invalid_notification_type",
            f"Unknown notification type '{notification_type}'. Allowed types: {', '.join(NOTIFICATION_TYPES)}.",
        )

    now = _utcnow()
    text = _coerce_str(message).strip() or f"{issue['category']} issue {issue['issue_id']}: {issue['description']}"
    record = {
        "notification_id": f"NOT-{uuid.uuid4().hex[:8].upper()}",
        "team_id": team["team_id"],
        "team_name": team["name"],
        "issue_id": issue["issue_id"],
        "message": text,
        "notification_type": ntype,
        "delivered": True,
        "delivered_at": now,
    }
    _issue_store().record_notification(record)
    return {
        "status": "ok",
        "notification_id": record["notification_id"],
        "team_id": record["team_id"],
        "notification_type": ntype,
        "delivered": True,
        "delivered_at": now,
    }


def _cutoff_for(period: str) -> str:
    days = {"week": 7, "month": 30, "quarter": 90}[period]
    earlier = datetime.now(timezone.utc).timestamp() - days * 86400
    return datetime.fromtimestamp(earlier, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _compiled_trends(top_category: str | None, top_count: int) -> list[str]:
    trends: list[str] = []
    if top_category:
        sentence = None
        try:
            payload = _load_json("sustainability.json")
            sentence = (payload.get("trends") or {}).get(top_category)
        except (OSError, json.JSONDecodeError, AttributeError, TypeError):
            # Missing or malformed qualitative data must not break the report;
            # the computed counts below are always available.
            sentence = None
        if sentence:
            trends.append(str(sentence))
        trends.append(f"{top_category} leads the recorded issue counts this period with {top_count} issue(s).")
    return trends


@_logged
def get_sustainability_report(
    period: str = "month", category: str = "", location_id: str = ""
) -> dict:
    """Summarize recorded sustainability issues for a requested period.

    Use when the user asks for campus sustainability statistics, trends, or the
    biggest problems over a period. Counts and rankings are computed from the
    recorded issues (never invented). Optional ``category`` and ``location_id``
    narrow the computation. Returns a success envelope with category and
    priority counts, the open issue count, top locations, and notable trends.

    IMPORTANT: You MUST provide all parameters in the JSON call. If you are not filtering
    by category or location_id, pass an empty string "" for those parameters.
    """
    period_key = _coerce_str(period).strip().lower() or "month"
    if period_key not in PERIODS:
        return _error("invalid_period", f"Unknown period '{period}'. Allowed periods: {', '.join(PERIODS)}.")

    normalized_category = None
    if category is not None and _coerce_str(category).strip():
        normalized_category = _coerce_str(category).strip().upper()
        if normalized_category not in CATEGORIES:
            return _error(
                "invalid_category", f"Unknown category '{category}'. Allowed categories: {', '.join(CATEGORIES)}."
            )

    if location_id is not None and _coerce_str(location_id).strip() and _location_by_id(_coerce_str(location_id)) is None:
        return _error("unknown_location_id", f"No campus location matches location_id '{location_id}'.")

    issues = _issue_store().all()
    if period_key != "all":
        cutoff = _cutoff_for(period_key)
        issues = [item for item in issues if (item.get("created_at") or "") >= cutoff]
    if normalized_category is not None:
        issues = [item for item in issues if item.get("category") == normalized_category]
    if location_id is not None and _coerce_str(location_id).strip():
        needle = _coerce_str(location_id).strip().lower()
        issues = [item for item in issues if str(item.get("location_id", "")).lower() == needle]

    category_counts: dict[str, int] = {cat: 0 for cat in CATEGORIES}
    priority_counts: dict[str, int] = {}
    location_counts: dict[str, int] = {}
    open_count = 0
    for item in issues:
        category = item.get("category")
        if category in category_counts:
            category_counts[category] += 1
        priority = item.get("priority")
        if priority:
            priority_counts[priority] = priority_counts.get(priority, 0) + 1
        location_id_key = item.get("location_id")
        if location_id_key:
            location_counts[location_id_key] = location_counts.get(location_id_key, 0) + 1
        if str(item.get("status", "")).upper() not in ("RESOLVED", "CLOSED"):
            open_count += 1

    top_locations = []
    for location_id_key, count in sorted(location_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:5]:
        location = _location_by_id(location_id_key)
        top_locations.append(
            {
                "location_id": location_id_key,
                "display_name": location["display_name"] if location else location_id_key,
                "count": count,
            }
        )

    ordered = sorted(category_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    top_category = ordered[0][0] if ordered and ordered[0][1] > 0 else None

    return {
        "status": "ok",
        "period": period_key,
        "category_counts": category_counts,
        "priority_counts": priority_counts,
        "open_issue_count": open_count,
        "top_locations": top_locations,
        "notable_trends": _compiled_trends(top_category, ordered[0][1] if top_category else 0),
    }


Tools = [
    lookup_campus_location,
    create_issue,
    get_issue,
    search_issues,
    update_issue,
    notify_team,
    get_sustainability_report,
]
