# CampusGreen Evaluation Matrix (Phase 5)

A runnable evaluation: every column of the matrix maps a real-user scenario to the
tests that prove it, so a judge (or another engineer) can replay the evidence
without taking the document's word for it. Numbers are from the Phase 5 run on
Windows 11 / Python 3.12.13 / agentkernel 0.8.1 — see [TEST_REPORT.md](TEST_REPORT.md).

| # | Scenario (user says / experiences) | What it must prove | Proof (file + test) |
| --- | --- | --- | --- |
| 1 | **Report a water leak at a known location** — "There's a water leak outside Lab 3." | Lookup → create → notify chain runs; issue persisted with correct category/location/priority/team/status; only then does the agent claim success. | `e2e_scenario_test.py::test_competition_scenario_end_to_end` step 1; `integration_test.py::test_report_workflow_lookup_create_notify`; `tool_test.py::test_create_issue_persists_to_disk`; `agent_behavior_test.py::test_report_resolves_category_priority_location[…]` |
| 2 | **Category + priority inferred from free text** — 9 phrasings across all categories | Deterministic routing matches SPEC §12 examples (WATER dripping→MEDIUM, overflow blocking walkway→HIGH, smoke→HIGH, …) | `agent_behavior_test.py::test_report_resolves_category_priority_location` (9 param cases) |
| 3 | **Status by ticket ID and by memory** — "What's the status of WTR-001?" then follow-up "What's the current status?" | get_issue returns real stored data; an unfollowed-up ID is never fabricated; multi-turn resolves the session's active issue. | `agent_behavior_test.py::test_status_by_explicit_id_uses_stored_data`, `test_multi_turn_escalation_continuity`; `integration_test.py::test_multi_turn_status_resolves_active_issue` |
| 4 | **Unknown location** — "There's a leak near the old building." | Agent clarifies, never creates a ticket, never guesses the location. | `integration_test.py::test_unknown_location_creates_no_issue`; `agent_behavior_test.py::test_unknown_location_clarifies_and_creates_nothing`; `security_test.py::test_injection_cannot_force_create_at_unverified_location` |
| 5 | **Unknown issue ID** — "What's the status of WTR-999?" | No fabricated status or "opened" claim; error reported truthfully. | `agent_behavior_test.py::test_unknown_issue_is_answered_without_hallucination`; `demo_test.py::test_unknown_issue_status_not_fabricated` (Tier-2 twin) |
| 6 | **Missing information** — "There is a problem." | Agent asks for category + location; no tool runs without both. | `agent_behavior_test.py::test_missing_information_triggers_questions`; `demo_test.py::test_missing_information_asks_for_clarification` |
| 7 | **Lifecycle enforcement** — every pair of statuses; CLOSED as terminal; resolve-then-close | Only SPEC §13 transitions allowed; illegal moves rejected and leave the file byte-for-byte unchanged. | `tool_test.py::test_lifecycle_state_machine` (36 cases), `test_closed_is_terminal`, `test_escalated_can_reach_resolved_and_resolve_requires_closure`, `test_rejected_lifecycle_move_does_not_change_file` |
| 8 | **Escalation continuity** — "…getting worse… water is spreading…" | Update to ESCALATED/CRITICAL, escalation recorded, subsequent status reflects it. | `e2e_scenario_test.py` step 7–9; `agent_behavior_test.py::test_multi_turn_escalation_continuity`; `integration_test.py::test_escalation_updates_and_notifies` |
| 9 | **Notification reliability** — success claims + failures | `delivered=True` only when recorded; failure records nothing; partial failure (created but not notified) reported truthfully and verified on disk. | `tool_test.py::test_notify_team_ok`, `test_notify_failure_records_nothing`; `integration_test.py::test_partial_failure_create_ok_notify_failed`; `e2e_scenario_test.py` step 10 |
| 10 | **Duplicate reports** — same report twice | V1 documents the behavior: two distinct tickets, no silent merge (out of SPEC). | `integration_test.py::test_duplicate_report_creates_two_tickets`; `agent_behavior_test.py::test_duplicate_report_yields_two_distinct_tickets`; `tool_test.py::test_create_issue_duplicate_reports_get_distinct_ids` |
| 11 | **Analytics from real records** — "biggest sustainability problems this month?" | Counts computed only from recorded issues; top category correct for the seed; malformed trend data can't break the report. | `tool_test.py::test_seed_report_counts_all_periods`, `test_report_survives_missing_or_malformed_trends`; `agent_behavior_test.py::test_analytics_are_computed_from_real_records`; `e2e_scenario_test.py` step 3 |
| 12 | **Per-sender session isolation** — two students, no bleed | Sessions keyed by sender number; A's active issue never answers B. | `integration_test.py::test_session_isolation_between_senders`; `agent_behavior_test.py::test_session_isolation_between_senders`; `tool_test.py::test_active_issue_cache_is_isolated_between_sessions` |
| 13 | **Tool failure under pressure** — forced create/lookup/get/update/notify failures, role-override & impersonation | Agent declines overrides, never forces a create at an unverified location, reports every failure truthfully, never claims a notification that didn't happen. | `security_test.py::test_role_override_attempt_is_declined`, `test_pretend_override_is_declined`, `test_does_not_contact_individuals`, `test_meter_data_is_not_invented`, `test_injection_cannot_force_create_at_unverified_location`, `test_injection_cannot_claim_predecessor_notification`; `integration_test.py::test_tool_failure_is_reported_truthfully`; `agent_behavior_test.py::test_failed_create_is_not_claimed` |
| 14 | **Data & cross-file integrity** | Every reference resolves, every seed status is reachable from REPORTED, taxonomy matches the 7 categories, trend sentences cover all categories. | `data_integrity_test.py` (16 tests) |
| 15 | **Security & hygiene** | No secrets in tracked files; `.env` ignored; `.env.example` placeholders empty for secrets; log lines never carry descriptions/keys; startup reports only missing-variable names. | `security_test.py::test_no_secrets_in_committed_source`, `test_env_file_is_ignored_and_untracked`, `test_env_example_contains_only_placeholders`, `test_success_log_lines_are_structured_and_sanitized`, `test_validate_config_reports_only_names`, `test_startup_exits_cleanly_without_config` |
| 16 | **Performance smoke** | 200 sequential creates complete quickly and ID sequencing stays correct. | `tool_test.py::test_many_creates_complete_quickly` |
| 17 | **Full competition journey (Tier-1, real WhatsApp handler)** | The 11-step scenario end-to-end, finishing with a persisted-state audit on disk. | `e2e_scenario_test.py` |
| 18 | **LLM truthfulness & injection (Tier-2, gated)** | A real OpenAI-backed agent reports status failures without fabrication, asks for missing info, and refuses prompt-injection / false-notification instructions. | `demo_test.py` conversational tests (11, require `OPENAI_API_KEY` + CLI); `security_test.py::test_llm_refuses_prompt_injection`, `test_llm_wont_claim_false_notification` |

## Reading the numbers

- **Tier 1 (rows 1–17):** every row is verified by passing tests in this repo's
  `use-cases/campusgreen/`. Run `uv run pytest -q --exitfirst` to replay.
- **Tier 2 (row 18):** guaranteed-skipped on this Windows machine (no
  `OPENAI_API_KEY`, no `readline`), runnable in keyed CI on a POSIX runner. The
  Tier-1 rows pin the same application behavior deterministically, so none of the
  matrix stalls on an un-runnable environment.

## Verdict

All 17 runnable scenarios pass offline (208 of 221 tests passing, 13 skips are
exclusively Tier-2 LLM tests). The two Phase-5 decisions that are *known product
limits* — no deduplication, and no full agent-behavior claim from the deterministic
tier alone — are stated in [TEST_REPORT.md](TEST_REPORT.md) rather than papered over.