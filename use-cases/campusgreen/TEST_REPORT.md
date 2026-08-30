# CampusGreen Reliability & Test Report (Phase 7 + Phase 8)

Evidence that the CampusGreen agent is reliable, observable, secure, and ready for
competition evaluation — with the exact commands, numbers, and known limitations.

- **Suite command:** `uv run pytest -q` (or `.venv\Scripts\python.exe -m pytest -q` on Windows)
- **Result on this machine (Windows 11, Python 3.12.13, agentkernel 0.8.1):**

| | Count |
| --- | --- |
| Collected | 280 |
| **Passed** | **265** |
| Skipped | 15 (all LLM-dependent; see [gating](#gating-llm-only-tests)) |
| Failures | 0 |
| Duration | ~13–20 s (machine-dependent) |

## The proof is deliberately two-tiered

Agent behavior can never be *proven* by determinism alone, and LLM runs can never
be proven without a real model. So this suite splits the evidence the only honest
way there is:

### Tier 1 — Deterministic and runnable (no API key, no network) · 265 tests

Every Tier-1 test drives the **real application path**: the real `AgentWhatsAppRequestHandler`
routing, the real seven `tool.py` tools, the real persisted JSON data, and real
cross-session state. The only stand-in is `CampusGreenDriver`, a scripted replica
of the *reasoning step* (research tool → decide → reply), never of the tools or the
data. This layer proves:

- the WhatsApp boundary routes, isolates sessions, and returns replies correctly;
- every tool resolves, validates, persists, and reports successes/failures truthfully;
- lifecycle rules, analytics, location verification, and notification records
  behave exactly as SPEC.md requires;
- prompt-injection, override, and impersonation attempts are declined and can never
  force an action through an unverified location;
- nothing that should never be written to logs, files, or git ever is.

It does **not** claim to prove the LLM will reason well — it proves the application
around the LLM is sound. The same scenarios are exercised against a real model in
Tier 2.

### Tier 2 — LLM-dependent (conversational, same scenarios) · 15 gated tests

`demo_test.py` drives the real OpenAI-backed agent through Agent Kernel's built-in
test harness (`Test("demo.py")`) — including two Phase 8 probes that ask the model
to (gated order 12) produce a plan grounded in the recorded issues and (gated order
13) act (escalate) only for the explicit request and verify the persisted ticket —
and `security_test.py` adds two real prompt-injection probes. These are skipped
unless `OPENAI_API_KEY` is set **and** the platform can import `agentkernel.cli`
(its `readline` dependency makes it unavailable on stock Windows). In CI with the
key and a POSIX runner they execute; here they are the 15 skips. Their expectations
are checked against the same truths as their Tier-1 twins, so crossing either tier
verifies the same contract.

## Coverage by file

| File | Tests | What it verifies |
| --- | --- | --- |
| `tool_test.py` | 109 | Full unit matrix for all **seven** tools: location lookup (known/alias/id/case/unknown/empty/non-string), create (all 7 categories, ID sequencing, persistence, distinct duplicate IDs, validation codes), get (existing/nonexistent/malformed/mixed-case), **`search_issues`** (filter by category/status/location, `OPEN` = not RESOLVED/CLOSED, `limit` cap + `total_matches`, deterministic priority-rank-then-newest order, empty stores, validation codes, non-string argument), update (**36-case lifecycle state machine** + terminal CLOSED + resolve-then-close + no-op same-status), notify (success record, unknown team/issue/type, no-write-on-failure), sustainability reports (seed counts for all periods, filters, validation, malformed-trends fallback), non-string torture of every parameter, the per-session `active_issue_id` cache (written and isolated across sessions), no-write-on-failure file checks, and a 200-create performance smoke test |
| `agent_behavior_test.py` | 21 | What the agent actually *does* per prompt (tools called, with which args): general questions use no tools, 9 free-text reports resolve to the correct category+priority+location, status by ID and by session memory, unknown issue/location handled without fabrication, missing-info clarification, multi-turn escalation continuity, senders A/B session isolation, partial-failure and failed-create truthfulness, analytics from real counts, notification claims match records, distinct duplicate tickets |
| `integration_test.py` | 14 | Offline WhatsApp boundary: routing, per-sender session isolation, reply send-back, missing-agent and error mapping, audio rejection, then **real-tool workflows through the handler**: report chain, unknown location, status from stored data, escalation, truthful tool failure, *partial failure (created-but-not-notified)*, *duplicate reports*, *multi-turn status resolution* |
| `data_integrity_test.py` | 16 | The seed data contract: canonical 7-category taxonomy, every location→team and issue→location/team reference resolves, ID prefixes match categories, unique IDs, valid statuses/priorities, ≥10 locations, ≥1 issue per non-OTHER category, well-formed ISO history, **every seeded status reachable from REPORTED** through the SPEC §13 diagram, all 7 trend sentences present |
| `security_test.py` | 16 (14 run + 2 gated) | No secrets in committed source (OpenAI/AWS/Google/Slack/GitHub/PEM patterns via `git ls-files`), `.env` ignored and untracked, `.env.example` ships only empty secret placeholders, role-override/pretend/impersonation/meter-data/injection attempts declined, log lines structured and sanitized (no descriptions/keys in `caplog`), startup config reports names only and exits `2` without values, plus the two **gated LLM** injection probes |
| `e2e_scenario_test.py` | 1 | The 11-step competition scenario end to end through the WhatsApp handler — report → follow-up status → analytics → second (different-category) report → "was the team notified?" → status by ID → escalation → escalation notification recorded → post-escalation status → truthful partial failure → **final persisted-state audit on disk** |
| `conversational_flows_test.py` | 18 | Phase 7 **polished per-flow** matrix via `CampusGreenDriver` over the real tools: complete report, incomplete→clarification (no ticket), multi-turn clarification continuity (a location-only follow-up completes the same report), contextual status, natural status phrasings (by ID and by topic), escalation, **multi-issue reference resolution by topic** (the leak → WATER, the bins → WASTE), ambiguous multi-issue handling, unsupported request declined with no tool call, unknown location/issue never fabricated, tool failure and partial failure reported truthfully, courtesy messages use no tools, capabilities question uses no tools, and retry-after-failure does not duplicate work |
| `flow_scenarios_test.py` | 7 | Phase 7 **polished user-flow scenarios** driven through the real WhatsApp handler — complete report, incomplete→clarification, multi-turn clarification, contextual status, escalation, sustainability report (read-only, real counts), and unknown location (no fabricated ticket), each cross-checked against the persisted `issues.json` on disk |
| `action_planning_test.py` | 18 | Phase 8 **sustainability action planning** via `CampusGreenDriver` over the real tools: evidence-grounded plans (counts + real tickets + open status) that use **data tools only** (no create/update/notify while planning), focused category plans, location plans, "why" follow-ups that cite recorded counts, honestly declined cost/savings questions, truthful empty-store answers, escalation scoped to the explicit request (with disk verification of status/priority/history/notification), a full plan→why→act scenario through the WhatsApp handler, and clarification rather than guessing on vague escalation requests |

## Reliability hardening applied (code changes this phase)

| Concern | Change | Where |
| --- | --- | --- |
| Non-string / malformed tool inputs from an LLM | Every tool argument coerced via `_coerce_str`; structured error envelopes instead of `AttributeError` | `tool.py` |
| SPEC §13 lifecycle | `ALLOWED_TRANSITIONS` enforced in `update_issue`; illegal moves rejected with `invalid_transition`; CLOSED is terminal; same-status updates remain no-ops | `tool.py` |
| Failed actions silently claimed | Tools return ok only when the write/delivery actually happened; the agent instructions say never to claim a completed action without a confirming tool result | `tool.py`, `agent.py` |
| Observability with no secret leakage | One structured log line per tool call (`tool=… status=… issue_id=… team=… error=…`); never descriptions, credentials, or key material | `tool.py` |
| Missing qualitative data breaks reports | `_compiled_trends` falls back to computed counts when `sustainability.json` is missing/malformed | `tool.py` |
| Wrong author attribution | `reported_by` falls back to the acting channel user, else `"student"` | `tool.py` |
| Override / injection attempts | Three new rules: ignore override attempts, never reveal instructions/config/credentials/other users' data, decline to confirm unconfirmed actions | `agent.py` |
| Obscure startup failure | `server.py` now validates required env vars and prints **names only** before `SystemExit(2)`; `validate_config` is importable and tested | `server.py`, `security_test.py` |
| Polished conversational flows (Phase 7) | `agent.py` instructs the LLM to continue an in-progress task, resolve multi-issue references by topic (asking when ambiguous), acknowledge courtesies with **no tool call**, politely decline out-of-scope requests, and retry only the failed step; `CampusGreenDriver` mirrors all of these deterministically over the real tools | `agent.py`, `test_helpers.py` |
| Inspecting the data behind a plan (Phase 8) | New `search_issues` tool lists the **actual recorded tickets** (compact, no full history) filtered by category/status/location with a `total_matches` count and a documented, deterministic sort (priority rank, then newest) — no fabricated rows, `invalid_*` errors for bad filters | `tool.py`, `tool_test.py` |
| Plans that are evidence, not fiction (Phase 8) | `agent.py` ACTION PLANNING block instructs the agent to separate evidence (counts, open status, real ticket IDs/locations) from recommendations, decline cost/savings/forecast questions the data can't compute, and **never act inside a plan**; `CampusGreenDriver` mirrors the same rules deterministically | `agent.py`, `test_helpers.py`, `action_planning_test.py` |
| Acting on a plan (Phase 8) | Only an explicit user request ("escalate the top …") triggers `update_issue` + `notify_team`; plan→why→act scenarios are verified on disk (status, priority, history entry, notification record) | `test_helpers.py`, `action_planning_test.py` |

## Known limitations (documented, not hidden)

- **No deduplication.** V1 deliberately creates a new ticket for each report, even an
  exact repeat; SPEC.md does not require dedup. This is *pinned by regression tests*
  (`test_duplicate_report_creates_two_tickets`, `test_duplicate_report_yields_two_distinct_tickets`,
  `test_create_issue_duplicate_reports_get_distinct_ids`) so the behavior is explicit, not accidental.
- **Local JSON persistence** is a prototype store; concurrent writers are not safe and no
  database/queue is used. Fine for a demo/competition, not for production concurrency.
- **`notify_team` writes through a `mock://` channel** — "delivered" means recorded in the
  notification log, not delivered to a human.
- **LLM-language variability.** Tier-1 expectations pin the *application*; the exact wording
  of real-model replies is only loosely asserted in Tier 2.
- **Windows CLI caveat.** `agentkernel.cli` imports the Unix-only `readline`, so `demo.py`
  (CLI) cannot start on stock Windows. The same agent is fully covered by the WhatsApp path
  and `integration_demo.py`.

## Secrets & hygiene (verified by tests)

- No OpenAI/AWS/Google/Slack/GitHub/PEM secret patterns in any git-tracked file.
- `.env` is git-ignored (untracked); only empty-placeholder `.env.example` is committed.
- Log lines carry only tool/allowed-identifier fields, verified with `caplog` including
  planted `sk-…` sentinel strings.
- `server.py` reports missing config by name, tests assert no value ever leaks.

## Re-running

```bash
uv run pytest -q            # full suite
uv run pytest tool_test.py -q
uv run pytest -q --exitfirst # break on first failure while iterating
```

Mutation caveat: exciting verbose test runs create tickets only inside the isolated
`CAMPUSGREEN_DATA_DIR` temp copies; the committed `data/issues.json` is never written
by the suite.