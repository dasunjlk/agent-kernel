# Phase 9A — Codebase Audit & Remediation (Telegram Update)

Audit of the `use-cases/campusgreen/` Agent Kernel application before the Phase 9B testing
campaign. Focus: Groq provider migration, Telegram messaging integration, security, and
architectural integrity. This document continues the Phase 9A state with the scope update
replacing the target messaging integration from **WhatsApp** to **Telegram**.

All changes are confined to `use-cases/campusgreen/`. No Agent Kernel core, framework, other use
case, or frozen docs were touched.

---

## 1. Repo & branch baseline

| Item | State |
| --- | --- |
| Branch | `feat/campusgreen-phase9a-audit` (created for this audit) |
| Parent branch | `feat/campusgreen-bug-fixing` @ `a89481a1` (Phase 8 action planning) |
| `origin/develop` | `acbb0f9b` — contains **none** of this use case (all files exist only on the parent branch) |
| Remote | `dasunjlk/agent-kernel` (fork) |
| Working tree after changes | clean except intended edits (all under `use-cases/campusgreen/`) |
| Env vars present | none: no `OPENAI_API_KEY`, `GROQ_API_KEY`, or `TELEGRAM_BOT_TOKEN` |
| `.env` | does not exist |

> **Decision recorded.** Phase 9A was branched from the Phase-8-inclusive local branch, not from
> `origin/develop`, because `develop` does not yet contain the CampusGreen app. This keeps the
> full feature set (including action planning) available to the audit.

---

## 2. Groq provider migration — config-ready + env-gated

**Finding (ground truth, verified in the openai-agents SDK source):**
- The SDK defaults to the **Responses API** (`_openai_shared.set_use_responses_by_default(True)`),
  which Groq does not serve.
- `get_default_model()` reads `OPENAI_DEFAULT_MODEL`, else `gpt-5.6-luna` (a Responses-only model),
  so the app silently tied itself to OpenAI's default.
- `OpenAIChatCompletionsModel` and `set_default_openai_api("chat_completions")` are public SDK
  APIs. `OpenAIRunner.run`/`run_streamed` invoke `Runner.run(agent.agent, ...)` without overriding
  the model, so setting `model=` on the raw `agents.Agent` is honored end-to-end.

**Change applied (all in `use-cases/campusgreen/`):**
- `agent.py`: added `_resolve_model()`. When `GROQ_API_KEY` is set it returns an explicit
  `OpenAIChatCompletionsModel` pointed at `https://api.groq.com/openai/v1` with
  `GROQ_MODEL` (default `llama-3.3-70b-versatile`); otherwise it returns `None` so the OpenAI
  default is unchanged. The model is attached via `Agent(..., model=...)`. No forcing, no global
  SDK flag, no core change.
- `.env.example`: added `GROQ_API_KEY=` and `GROQ_MODEL=` empty placeholders.
- `groq_readiness_test.py`: 6 deterministic offline checks (default path returns `None` and
  keeps OpenAI; key path returns a chat-completions model with the Groq model string and base URL;
  the client host is Groq, not `api.openai.com`; the seven tools are preserved) plus 1 live
  probe gated on `GROQ_API_KEY` + the Unix-only CLI. **No real Groq key exists in this
  environment, so the live probe is skipped — readiness is reported honestly.**
- `README.md` / `INTEGRATION.md`: LLM providers documentation.

**Remaining (intentionally not changed):** the Tier-2 conversational tests keep using OpenAI's
Responses API, so they remain OpenAI-gated. Groq is an *alternative path*, not a replacement for
the OpenAI-backed test tier.

---

## 3. Telegram messaging integration (Phase 9A scope update)

**Assessment: fully implemented & verified offline; long-polling ready.**

Target architecture:
```text
Telegram User
      ↓
Telegram Bot API
      ↓
Telegram Messaging Adapter (CampusGreenTelegramHandler)
      ↓
Agent Kernel (AgentTelegramRequestHandler)
      ↓
CampusGreen Agent
      ↓
Existing CampusGreen Tools
      ↓
Existing State / Memory (issues.json / locations.json / teams.json)
      ↓
Response
      ↓
Telegram Adapter
      ↓
Telegram Bot API
      ↓
Telegram User
```

Verified offline (uses the real `AgentTelegramRequestHandler` via `object.__new__`, no credentials):
- `telegram_handler.py` shim (`CampusGreenTelegramHandler`):
  - Message normalization (whitespace stripped, empty text ignored).
  - Duplicate platform-event handling: bounded in-memory de-duplication on `update_id` and `message_id` (`_DEDUP_MAX=10000`).
  - `/start` command returns a friendly CampusGreen introduction without running the agent unnecessarily.
  - `/help` command returns guidance on reporting and action planning.
  - Long polling runner `poll()` using `getUpdates` for frictionless local development without public HTTPS webhooks or tunnels.
- `server.py`: supports long polling by default (`uv run python server.py`) and optional webhook mode (`uv run python server.py --webhook`); validates `TELEGRAM_BOT_TOKEN` (or `AK_TELEGRAM__BOT_TOKEN`) and `OPENAI_API_KEY` (or `GROQ_API_KEY`); clean `SystemExit(2)` with names only.
- Session mapping: `session_id = str(chat_id)` isolates conversations per chat; `_acting_user()` maps `user_id` (`from.id`) to `reported_by`; `CAMPUSGREEN_CHANNEL=telegram` stamps `source_channel`.
- `integration_test.py`, `flow_scenarios_test.py`, `e2e_scenario_test.py` prove routing, session isolation, the boundary shim, and full tool workflows offline.

---

## 4. Telegram configuration audit

| Audit Item | Status | Notes |
| --- | --- | --- |
| Telegram code implemented | **YES** | `telegram_handler.py`, `server.py`, `integration_demo.py` |
| Telegram bot token configured | **NO** | Only placeholders in `.env.example`; real token kept local |
| Telegram polling configured | **YES** | Built into `CampusGreenTelegramHandler.poll()` and `server.py` default mode |
| Telegram message receiving implemented | **YES** | Webhook & getUpdates update parsing to `AgentRequestText` |
| Telegram response sending implemented | **YES** | `_send_message` with split-message support |
| Session mapping implemented | **YES** | `chat_id -> session_id`, `from.id -> acting_user_id` |
| Live Telegram testing possible | **NO** | Real `TELEGRAM_BOT_TOKEN` required for live API testing |

---

## 5. Security posture

Verified (no code change required in core):
- `security_test.py` enforces: no secrets in committed source, `.env` ignored + untracked,
  `.env.example` placeholders-only, structured/sanitized `ak.campusgreen.tools` logs (no
  free-text descriptions or key material, `error=` codes), `validate_config` reports names only,
  clean startup exit, and anti-injection / anti-fabrication input-boundary behavior.
- `agent.py` rules forbid inventing locations, IDs, tickets, notifications, or statistics; tools
  return `{"status": "ok|error"}` envelopes and the agent never claims success without one.
- **No committed secrets or key material found** across `use-cases/campusgreen/`.

---

## 6. Test coverage / gating inventory

Measured on this machine with `uv run pytest -q`: **289 total — 273 passed, 16 skipped**

- **Tier 1 — deterministic (273), offline, no key/network:** `demo_test.py` (unit), `tool_test.py`,
  `agent_behavior_test.py`, `action_planning_test.py`, `conversational_flows_test.py`,
  `flow_scenarios_test.py`, `integration_test.py`, `security_test.py` (deterministic half),
  `e2e_scenario_test.py`, `data_integrity_test.py`, and `groq_readiness_test.py` (deterministic
  half). Driven by `CampusGreenDriver` / `FakeAgentService` over the **real** tools with isolated
  `CAMPUSGREEN_DATA_DIR`/re-seeded fixtures; never calls Telegram/Meta/OpenAI.
- **Tier 2 — gated (16):** conversational agent tests + LLM probes + live Groq probe, skipped
  without `OPENAI_API_KEY`/`GROQ_API_KEY` and the Unix `readline` (not stock Windows).

---

## 7. Action items executed for Telegram migration

| # | File | Change | Type |
| --- | --- | --- | --- |
| 1 | `telegram_handler.py` (new) | CampusGreenTelegramHandler boundary shim with normalization, dedup, /start, and polling | feat |
| 2 | `whatsapp_handler.py` (deleted) | Removed obsolete WhatsApp handler shim | chore |
| 3 | `server.py` | Telegram server supporting default long polling and optional webhook | feat |
| 4 | `config.yaml` | `telegram.agent: "campusgreen"` | chore |
| 5 | `.env.example` | `TELEGRAM_BOT_TOKEN=`, `CAMPUSGREEN_CHANNEL=telegram` | chore |
| 6 | `integration_demo.py` | Local Telegram demo driver with simulated webhook updates | feat |
| 7 | `test_helpers.py` | Telegram handler construction and testing fixtures | test |
| 8 | `integration_test.py` | Complete offline Telegram boundary test suite (21 tests) | test |
| 9 | `security_test.py` | Updated config validation tests for `TELEGRAM_BOT_TOKEN` | test |
| 10 | `flow_scenarios_test.py`, `e2e_scenario_test.py`, `action_planning_test.py` | Updated docstrings and handler fixtures | test |
| 11 | `README.md`, `INTEGRATION.md`, `SPEC.md`, `DEMO.md`, `TEST_REPORT.md` | Updated documentation to describe Telegram integration | docs |

Nothing outside `use-cases/campusgreen/` was modified.

---

## 8. Final Phase 9A Verification

### Messaging
- **Previous transport:** WhatsApp
- **New transport:** Telegram

### Telegram
- **Bot implementation:** `CampusGreenTelegramHandler` in `telegram_handler.py`
- **Token configuration:** `TELEGRAM_BOT_TOKEN` (or `AK_TELEGRAM__BOT_TOKEN`)
- **Polling:** Implemented via `CampusGreenTelegramHandler.poll()` using `getUpdates`
- **Message receiving:** Webhook and polling conversion to `AgentRequestText`
- **Message sending:** Outbound `_send_message` with split-message handling
- **Session mapping:** `chat_id -> session_id`, `from.id -> acting_user_id`
- **Error handling:** Safe user-facing error messages, no leaked stack traces
- **Duplicate update handling:** Bounded in-memory dedup on `update_id` and `message_id`

### Agent Kernel
- **Agent Kernel bypassed:** NO
- **Direct LLM calls from adapter:** NO
- **Existing state reused:** YES (`issues.json`, `locations.json`, `teams.json`, `sustainability.json`)
- **Existing tools reused:** YES (`tool.py`)

### Groq
- **Provider:** Groq (via `OpenAIChatCompletionsModel` pointed at `https://api.groq.com/openai/v1`)
- **Model:** `llama-3.3-70b-versatile` (or `GROQ_MODEL`)
- **Tool calling:** Fully preserved over the 7 tools
- **Configuration status:** Ready (env-gated on `GROQ_API_KEY`)

### Security
- **Telegram token hard-coded:** NO
- **Telegram token committed:** NO
- **Telegram token logged:** NO
- **.env protected:** YES (.gitignore and untracked)
