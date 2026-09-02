# CampusGreen

CampusGreen is a university sustainability coordinator built on Agent Kernel. A single
`campusgreen` agent understands campus sustainability reports (water, energy, waste, food,
pollution, infrastructure), verifies locations, creates and tracks issues, coordinates the
responsible campus teams, answers analytics questions, and produces prioritized, evidence-grounded
action plans — backed by seven real Agent Kernel tools
and a local JSON data layer (Phase 3), served to end users over **Telegram** via Agent Kernel's
messaging integration (Phase 9A). The agent never claims a ticket was created or a team was
notified unless a tool actually did it.

## Prerequisites

- Python 3.12 or newer.
- `uv` for dependency management.
- An OpenAI API key (`OPENAI_API_KEY`) for local CLI conversation.

## Setup

```bash
./build.sh
```

## Run the local demo

```bash
uv run python demo.py
```

Inside the CLI, the default agent is `campusgreen`. Example messages:

```text
There's a water leak outside Lab 3.
The lights in Lab 4 have been left on overnight.
The bins near the Student Cafe are overflowing.
What's the status of WTR-001?
What are the biggest sustainability problems this month?
What should we prioritize to improve sustainability this month?
Escalate the top unresolved energy issue.
```

The demo persists issues and notifications to `data/issues.json` as you use it. To reset the data to
its seeded state, restore the file with `git checkout -- data/issues.json`.

## Telegram messaging integration

CampusGreen is also reachable over Telegram. The same agent and tools back both the CLI demo and
the Telegram server — only the channel boundary differs.

- **Run it locally with no Telegram credentials** (just `OPENAI_API_KEY` or `GROQ_API_KEY`):

  ```bash
  cp .env.example .env   # Windows: copy .env.example .env, then set OPENAI_API_KEY
  uv run python integration_demo.py
  ```

- **Run the real server with long polling** (needs `TELEGRAM_BOT_TOKEN`, no HTTPS tunnel required):

  ```bash
  uv run python server.py
  ```

See [`INTEGRATION.md`](INTEGRATION.md) for the full architecture, runtime configuration, and
offline testing notes.

## LLM providers

The agent reasons through the OpenAI Agents SDK, which serves OpenAI's Responses API by default.
To run the same agent against a chat-completions-only provider such as **Groq**, set a Groq key:

```bash
export GROQ_API_KEY=...            # overrides the OpenAI path
export GROQ_MODEL=llama-3.3-70b-versatile   # optional; defaults to llama-3.3-70b-versatile
```

When `GROQ_API_KEY` is set, `agent.py` builds an explicit
`OpenAIChatCompletionsModel` pointed at `https://api.groq.com/openai/v1` (Groq has no Responses
API). Without it, the agent keeps the default OpenAI behaviour unchanged, so both CLI and Telegram
paths work with just `OPENAI_API_KEY`. The model wiring is covered by `groq_readiness_test.py`:
its deterministic checks run offline, and a live Groq probe is gated on `GROQ_API_KEY` plus the
Unix-only CLI (skipped on stock Windows and without a key).

## Tools

The agent binds all seven tools through Agent Kernel's `OpenAIToolBuilder`. Each tool returns a
structured envelope: `{"status": "ok", ...}` on success or `{"status": "error", "error": "<code>",
"message": ...}` on failure; the lifecycle state of an issue lives inside the `issue` (`status`
field), separate from the envelope-level ok/error outcome.

| Tool | Purpose | Key inputs | Output / failure codes |
| --- | --- | --- | --- |
| `lookup_campus_location` | Resolve a place mention to a verified campus location | location name/alias | `location` (id, building, zone, responsible team) / `location_not_found`, `empty_query` |
| `create_issue` | Record a new sustainability issue with a generated ID (`CAT-NNN`) | category, description, location_id, priority | `issue`, `issue_id`, `category`, `location`, `assigned_team_id` / `unknown_location`, `invalid_category`, `invalid_priority`, `missing_description` |
| `get_issue` | Fetch a stored issue (status questions, follow-ups) | issue_id | `issue` / `issue_not_found`, `invalid_issue_id` |
| `search_issues` | List the actual issue records matching filters (category/status/location) | category, status, location_id, limit | `count`, `total_matches`, `issues` (compact, no history) / `invalid_category`, `invalid_status`, `unknown_location_id`, `invalid_limit` |
| `update_issue` | Change priority/status, add notes, escalate or resolve | issue_id, priority, status, additional_note, resolution_note | `issue`, `updated_at`, `history_entry` / `issue_not_found`, `no_changes`, `invalid_status`, `invalid_priority` |
| `notify_team` | Notify the responsible campus team via the local channel | team_id, issue_id, notification_type, message | `notification_id`, `delivered` / `unknown_team`, `unknown_issue` |
| `get_sustainability_report` | Answer analytics questions about recorded issues | period (week/month/quarter/all), category, location_id | `category_counts`, `priority_counts`, `open_issue_count`, `top_locations`, `notable_trends` / `invalid_period`, `invalid_category`, `unknown_location_id` |

A report flows through the chain `lookup_campus_location` -> `create_issue` -> `notify_team`,
verified end-to-end by the tool-chaining integration test. An **action plan** flows through
`get_sustainability_report` -> `search_issues` -> prioritized, evidence-grounded recommendations:
the agent separates what the data shows (counts, open tickets, locations) from what it recommends,
never fabricates metrics or savings, and performs no operational action (escalate/notify) unless
the user explicitly asks it to act.

## Data layer

- `data/locations.json` — campus locations with aliases, building, zone, and responsible team.
- `data/teams.json` — campus teams (facilities, grounds/landscape, food services, central services).
- `data/issues.json` — seeded issues plus the `notifications` delivery log (mutated by the demo).
- `data/sustainability.json` — qualitative sustainability trends merged into reports.

Issue IDs, category/priority validation, and lifecycle handling are computed in Python
(`tool.py`), never prompted from the LLM. Point the data layer elsewhere by setting
`CAMPUSGREEN_DATA_DIR` (used by the tests to isolate runs):

```bash
CAMPUSGREEN_DATA_DIR=/tmp/cg-data uv run python demo.py
```

The `tools` bound to the agent resolve the data directory from that environment variable at each
call, so a single build can run against any data snapshot without re-installing.

## Run tests

```bash
uv run pytest -q
```

The campusgreen test suite is **289 tests: 273 pass, 16 skip** on this machine
(~10 s), built in two honest tiers:

- **Tier 1 — deterministic (273 tests):** everything is testable without a key or
  network — the real Telegram handler routing, the real seven tools, the real JSON
  data layer, per-chat session isolation, the SPEC §13 lifecycle state machine,
  analytics, action planning, notification truthfulness, data-integrity,
  security/hygiene, the env-gated Groq model wiring, and an 11-step end-to-end
  competition scenario that finishes with a persisted-state audit. See
  `EVALUATION.md` for the scenario matrix.
- **Tier 2 — LLM-dependent (16 gated tests):** conversational tests via the Agent
  Kernel test harness plus two real prompt-injection probes (and, in Phase 8, an
  action-plan and an explicit-escalation probe), plus a live Groq connectivity
  probe. They run when
  `OPENAI_API_KEY` is set on a platform with the Unix `readline` module (not stock
  Windows), and are strictly skipped otherwise.

All tests run against isolated copies of the data files (via `CAMPUSGREEN_DATA_DIR`
and a re-seeded fixture), so the committed seed data is never mutated. The Telegram
tests need no credentials and call neither Telegram nor the OpenAI API. Full evidence,
per-file coverage, and known limitations are in [`TEST_REPORT.md`](TEST_REPORT.md).

## Project Layout

- `agent.py` — the `campusgreen` agent definition and system instructions (tools bound via `OpenAIToolBuilder`).
- `tool.py` — the seven Agent Kernel tools and the local JSON data layer.
- `demo.py` — Agent Kernel CLI entry point that registers the agent.
- `config.yaml` — Agent Kernel local configuration (in-memory sessions, logging, Telegram agent name).
- `test-config.yaml` — Agent Kernel test harness configuration.
- `demo_test.py` — deterministic tool/agent unit tests, conversational tests, and the tool-chaining integration test.
- `server.py` — Telegram entry point (long polling by default, optional `--webhook`).
- `integration_demo.py` — local Telegram demo (same routing, no Telegram credentials).
- `integration_test.py` — offline tests of the Telegram routing/session/error boundary and tool workflows.
- `telegram_handler.py` — `CampusGreenTelegramHandler` boundary shim over `AgentTelegramRequestHandler`.
- `INTEGRATION.md` — how the Telegram integration is wired, run, and tested.
- `tool_test.py` — 109-test unit matrix over all seven tools (incl. the lifecycle state machine).
- `agent_behavior_test.py` — 21 deterministic agent-behavior tests (tools called, truthfulness, isolation).
- `action_planning_test.py` — 18 Phase 8 action-planning tests (evidence-grounded plans, analysis-only behavior, explicit escalation, disk audits).
- `data_integrity_test.py` — 16 seed-data cross-reference/taxonomy integrity tests.
- `security_test.py` — 16 secret-scan / input-boundary / log-sanitization tests (incl. 2 gated LLM probes).
- `e2e_scenario_test.py` — the 11-step end-to-end competition scenario with a disk audit.
- `groq_readiness_test.py` — Phase 9A: env-gated Groq model wiring (deterministic, offline) + a live client probe gated on `GROQ_API_KEY`/CLI.
- `test_helpers.py` / `conftest.py` — shared offline harness + isolated-data fixtures.
- `TEST_REPORT.md` — reliability & testing evidence report.
- `DEMO.md` — how to run each demo surface.
- `EVALUATION.md` — evaluator scenario matrix mapped to the tests that prove each row.
- `data/` — seed locations, teams, issues, and sustainability trends.
- `SPEC.md` — product specification (source of truth for later phases).

## Scope of This Phase

The core agent and tool architecture is implemented: seven tools, a local data layer, tool-chaining
workflows, evidence-grounded action planning, the CLI-only demo, and a Telegram messaging
integration served through Agent Kernel's
native `AgentTelegramRequestHandler`. **Phase 5 (reliability & competition readiness)** hardened the
runtime — input coercion, SPEC §13 lifecycle enforcement, structured diagnostic logging, truthful
failure reporting, refusal of override/injection attempts, and config validation — and packaged the
whole thing as a runnable suite described in
[`TEST_REPORT.md`](TEST_REPORT.md). **Phase 7** added the polished conversational flows (multi-turn
clarification, topic reference resolution, courteous/out-of-scope handling). **Phase 8** added
sustainability action planning (data-grounded prioritization, honest boundaries, explicit-action
routing). **Phase 9A** added env-gated Groq model support and migrated the messaging integration
to Telegram. Deployment, a web dashboard, analytics platforms, multi-agent
architecture, and advanced memory workflows are intentionally deferred to later phases.