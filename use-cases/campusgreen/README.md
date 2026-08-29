# CampusGreen

CampusGreen is a university sustainability coordinator built on Agent Kernel. A single
`campusgreen` agent understands campus sustainability reports (water, energy, waste, food,
pollution, infrastructure), verifies locations, creates and tracks issues, coordinates the
responsible campus teams, and answers analytics questions — backed by six real Agent Kernel tools
and a local JSON data layer (Phase 3), served to end users over **WhatsApp** via Agent Kernel's
messaging integration (Phase 4). The agent never claims a ticket was created or a team was
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
```

The demo persists issues and notifications to `data/issues.json` as you use it. To reset the data to
its seeded state, restore the file with `git checkout -- data/issues.json`.

## WhatsApp messaging integration

CampusGreen is also reachable over WhatsApp. The same agent and tools back both the CLI demo and
the WhatsApp server — only the channel boundary differs.

- **Run it locally with no Meta credentials** (just `OPENAI_API_KEY`):

  ```bash
  cp .env.example .env   # Windows: copy .env.example .env, then set OPENAI_API_KEY
  uv run python integration_demo.py
  ```

- **Run the real server** (needs Meta credentials + a public HTTPS tunnel):

  ```bash
  uv run python server.py
  ```

See [`INTEGRATION.md`](INTEGRATION.md) for the full architecture, runtime configuration, and
offline testing notes.

## Tools

The agent binds all six tools through Agent Kernel's `OpenAIToolBuilder`. Each tool returns a
structured envelope: `{"status": "ok", ...}` on success or `{"status": "error", "error": "<code>",
"message": ...}` on failure; the lifecycle state of an issue lives inside the `issue` (`status`
field), separate from the envelope-level ok/error outcome.

| Tool | Purpose | Key inputs | Output / failure codes |
| --- | --- | --- | --- |
| `lookup_campus_location` | Resolve a place mention to a verified campus location | location name/alias | `location` (id, building, zone, responsible team) / `location_not_found`, `empty_query` |
| `create_issue` | Record a new sustainability issue with a generated ID (`CAT-NNN`) | category, description, location_id, priority | `issue`, `issue_id`, `category`, `location`, `assigned_team_id` / `unknown_location`, `invalid_category`, `invalid_priority`, `missing_description` |
| `get_issue` | Fetch a stored issue (status questions, follow-ups) | issue_id | `issue` / `issue_not_found`, `invalid_issue_id` |
| `update_issue` | Change priority/status, add notes, escalate or resolve | issue_id, priority, status, additional_note, resolution_note | `issue`, `updated_at`, `history_entry` / `issue_not_found`, `no_changes`, `invalid_status`, `invalid_priority` |
| `notify_team` | Notify the responsible campus team via the local channel | team_id, issue_id, notification_type, message | `notification_id`, `delivered` / `unknown_team`, `unknown_issue` |
| `get_sustainability_report` | Answer analytics questions about recorded issues | period (week/month/quarter/all), category, location_id | `category_counts`, `priority_counts`, `open_issue_count`, `top_locations`, `notable_trends` / `invalid_period`, `invalid_category`, `unknown_location_id` |

A report flows through the chain `lookup_campus_location` -> `create_issue` -> `notify_team`,
verified end-to-end by the tool-chaining integration test.

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

The Phase 5 reliability suite is **221 tests: 208 pass, 13 skip** on this machine
(~8.5 s), built in two honest tiers:

- **Tier 1 — deterministic (200 tests):** everything is testable without a key or
  network — the real WhatsApp handler routing, the real six tools, the real JSON
  data layer, per-sender session isolation, the SPEC §13 lifecycle state machine,
  analytics, notification truthfulness, data-integrity, security/hygiene, and an
  11-step end-to-end competition scenario that finishes with a persisted-state
  audit. See `EVALUATION.md` for the scenario matrix.
- **Tier 2 — LLM-dependent (13 gated tests):** conversational tests via the Agent
  Kernel test harness plus two real prompt-injection probes. They run when
  `OPENAPI_API_KEY` is set on a platform with the Unix `readline` module (not stock
  Windows), and are strictly skipped otherwise.

All tests run against isolated copies of the data files (via `CAMPUSGREEN_DATA_DIR`
and a re-seeded fixture), so the committed seed data is never mutated. The WhatsApp
tests need no credentials and call neither Meta nor the OpenAI API. Full evidence,
per-file coverage, and known limitations are in [`TEST_REPORT.md`](TEST_REPORT.md).

## Project Layout

- `agent.py` — the `campusgreen` agent definition and system instructions (tools bound via `OpenAIToolBuilder`).
- `tool.py` — the six Agent Kernel tools and the local JSON data layer.
- `demo.py` — Agent Kernel CLI entry point that registers the agent.
- `config.yaml` — Agent Kernel local configuration (in-memory sessions, logging, WhatsApp agent name).
- `test-config.yaml` — Agent Kernel test harness configuration.
- `demo_test.py` — deterministic tool/agent unit tests, conversational tests, and the tool-chaining integration test.
- `server.py` — WhatsApp entry point (real Meta webhooks via `AgentWhatsAppRequestHandler`).
- `integration_demo.py` — local WhatsApp demo (same routing, no Meta credentials).
- `integration_test.py` — offline tests of the WhatsApp routing/session/error boundary and tool workflows.
- `INTEGRATION.md` — how the WhatsApp integration is wired, run, and tested.
- `tool_test.py` — 100-test unit matrix over all six tools (incl. the lifecycle state machine).
- `agent_behavior_test.py` — 21 deterministic agent-behavior tests (tools called, truthfulness, isolation).
- `data_integrity_test.py` — 16 seed-data cross-reference/taxonomy integrity tests.
- `security_test.py` — 16 secret-scan / input-boundary / log-sanitization tests (incl. 2 gated LLM probes).
- `e2e_scenario_test.py` — the 11-step end-to-end competition scenario with a disk audit.
- `test_helpers.py` / `conftest.py` — shared offline harness + isolated-data fixtures.
- `TEST_REPORT.md` — reliability & testing evidence report (Phases 5).
- `DEMO.md` — how to run each demo surface.
- `EVALUATION.md` — evaluator scenario matrix mapped to the tests that prove each row.
- `data/` — seed locations, teams, issues, and sustainability trends.
- `SPEC.md` — product specification (source of truth for later phases).

## Scope of This Phase

The core agent and tool architecture is implemented: six tools, a local data layer, tool-chaining
workflows, the CLI-only demo, and a WhatsApp messaging integration served through Agent Kernel's
native `AgentWhatsAppRequestHandler`. **Phase 5 (reliability & competition readiness)** hardened the
runtime — input coercion, SPEC §13 lifecycle enforcement, structured diagnostic logging, truthful
failure reporting, refusal of override/injection attempts, and config validation — and packaged the
whole thing as a runnable 221-test suite described in
[`TEST_REPORT.md`](TEST_REPORT.md). Deployment, a web dashboard, analytics platforms, multi-agent
architecture, and advanced memory workflows are intentionally deferred to later phases.