# CampusGreen Evaluation Matrix (Phase 7 + Phase 8)

A runnable evaluation: every column of the matrix maps a real-user scenario to the
tests that prove it, so a judge (or another engineer) can replay the evidence
without taking the document's word for it. Numbers are from the Phase 7 + Phase 8
run on Windows 11 / Python 3.12.13 / agentkernel 0.8.1 — see
[TEST_REPORT.md](TEST_REPORT.md).

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
| 17 | **Full competition journey (Tier-1, real Telegram handler)** | The 11-step scenario end-to-end, finishing with a persisted-state audit on disk. | `e2e_scenario_test.py` |
| 18 | **LLM truthfulness & injection (Tier-2, gated)** | A real OpenAI-backed agent reports status failures without fabrication, asks for missing info, and refuses prompt-injection / false-notification instructions. | `demo_test.py` conversational tests (11, require `OPENAI_API_KEY` + CLI); `security_test.py::test_llm_refuses_prompt_injection`, `test_llm_wont_claim_false_notification` |

## Phase 7 — polished user flows

| # | Scenario (user says / experiences) | What it must prove | Proof (file + test) |
| --- | --- | --- | --- |
| P1 | **Incomplete report answers, not dead-ends** — "There is a problem." / "There is a leak." | The agent asks for exactly what's missing (category, then location) and creates nothing until both are known. | `conversational_flows_test.py::test_incomplete_report_asks_without_creating`; `agent_behavior_test.py::test_missing_information_triggers_questions`; `flow_scenarios_test.py::test_scenario_incomplete_report_clarifies` |
| P2 | **Multi-turn clarification continues the same report** — "There is a leak." → "Near Lab 3." | A follow-up that only supplies the missing location completes the **same** report (one ticket), per-session. | `conversational_flows_test.py::test_multi_turn_clarification_continues_report`, `test_clarification_scoped_to_session`; `flow_scenarios_test.py::test_scenario_multi_turn_clarification` |
| P3 | **Contextual status** — "What's the current status?" right after reporting | The follow-up resolves to the just-created issue without re-stating the ID. | `conversational_flows_test.py::test_contextual_status_resolves_just_created_issue`; `flow_scenarios_test.py::test_scenario_contextual_status`; `e2e_scenario_test.py` step 2 |
| P4 | **Natural status phrasings** — "What's happening with WTR-001?", "Is the leak issue resolved?" | By explicit ID and by topic both resolve to real stored state, never a guess. | `conversational_flows_test.py::test_status_by_explicit_id_natural_phrasing`, `test_status_by_topic_phrasing` |
| P5 | **Multi-issue reference resolution by topic** — report a leak, then overflowing bins, then ask "What is the status of the leak?" | "the leak" resolves to the WATER issue, "the bins issue" to the WASTE issue — not just "the most recent". | `conversational_flows_test.py::test_multi_issue_reference_resolved_by_topic`, `test_multiple_same_category_issues_ask_when_ambiguous` |
| P6 | **Unsupported request declined** — "Can you book me a university bus?" | Politely out-of-scope, steers back to sustainability, performs **no** tool action. | `conversational_flows_test.py::test_unsupported_request_honestly_declined` |
| P7 | **Courtesy / capabilities never waste a tool call** — "Thanks!", "Hello.", "What can you do?" | Natural short reply, no unnecessary tool invocation. | `conversational_flows_test.py::test_courtesy_messages_use_no_tools`, `test_capability_question_uses_no_tools` |
| P8 | **Retry after failure does not duplicate** — a failed create, then "Please try again." | The agent offers to redo the failed step without re-creating/fabricating an existing ticket. | `conversational_flows_test.py::test_retry_after_failure_does_not_duplicate`; `tool_test.py` `no-write-on-failure` group |

## Phase 8 — sustainability action planning

| # | Scenario (user says / experiences) | What it must prove | Proof (file + test) |
| --- | --- | --- | --- |
| AP1 | **Evidence-grounded action plan** — "What should we prioritize to improve sustainability this month?" | Returns a plan built from the recorded store (counts, how many still open, real ticket IDs, locations, priorities); separates evidence from recommendation; makes **no** operational tool call (no create/update/notify) while planning. | `action_planning_test.py::test_general_plan_ranks_top_category_with_evidence`, `test_general_plan_uses_only_data_tools` |
| AP2 | **Focused / location plans** — "What should we do about our energy use?" and "Which campus location needs attention?" | Recommendations name the responsible team, location, priority, and a concrete operational action for the matching tickets. | `action_planning_test.py::test_focused_plan_is_targeted_at_the_matching_category`, `test_location_plan_picks_top_location` |
| AP3 | **"Why" follow-up grounded in data** — "Why is ENERGY ranked first?" | The explanation uses recorded counts and open status ("3 report(s), 3 still open"), not invented rationale. | `action_planning_test.py::test_why_follow_up_uses_recorded_counts` |
| AP4 | **Honest boundaries on un-computable questions** — "How much money would fixing ENERGY save?" | Declines with "I can't calculate costs or savings from the available data" — no fabricated savings figures. | `action_planning_test.py::test_cost_savings_questions_are_declined_honestly` |
| AP5 | **Truthful empty-data answer** — planning with an empty store | An honest "could not find an open issue" answer; no invented tickets or counts. | `action_planning_test.py::test_empty_data_plan_is_truthful` |
| AP6 | **Explicit escalation acts — and only then** — plan → "why" → "Escalate the top unresolved energy issue." | The agent plans without acting, then escalates the exact referenced ticket (status, priority, history, notify record) and verifies the change on disk. | `action_planning_test.py::test_plan_then_why_then_act_scenario`, `test_escalation_from_plan_persists_to_disk`; `integration_demo.py` closing SEQUENCE turns |
| AP7 | **Vague escalation gets clarified, not guessed** — "Escalate something urgent." | Agent asks which issue (or which category) instead of picking arbitrarily. | `action_planning_test.py::test_vague_escalation_request_asks_for_clarification` |
| AP8 | **`search_issues` under all filters** (category / status / OPEN semantics / location / limit + `total_matches`) | Deterministic listing of real records with `status="OPEN"` = not RESOLVED/CLOSED; documented sort (priority rank, then newest). | `tool_test.py` `search_issues` group (9 tests incl. torture param) |

## Reading the numbers

- **Tier 1 (rows 1–17 + P1–P8 + AP1–AP8):** every row is verified by passing tests
  in this repo's `use-cases/campusgreen/`. Run `uv run pytest -q --exitfirst` to replay.
- **Tier 2 (row 18):** guaranteed-skipped on this Windows machine (no
  `OPENAI_API_KEY`, no `readline`), runnable in keyed CI on a POSIX runner. The
  Tier-1 rows pin the same application behavior deterministically, so none of the
  matrix stalls on an un-runnable environment.

## Verdict

All 32 runnable scenarios pass offline (265 of 280 tests passing; the 15 skips are
exclusively Tier-2 LLM tests). The Phase-5 decisions that are *known product
limits* — no deduplication, and no full agent-behavior claim from the deterministic
tier alone — are stated in [TEST_REPORT.md](TEST_REPORT.md) rather than papered over.