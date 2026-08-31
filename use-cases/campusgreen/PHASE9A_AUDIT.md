# Phase 9A — Codebase Audit & Remediation

Audit of the `use-cases/campusgreen/` Agent Kernel application before the Phase 9B testing
campaign. Focus: Groq provider migration, WhatsApp readiness, security, and architectural
integrity. This document is the companion to the changes made on
`feat/campusgreen-phase9a-audit` (branched from `feat/campusgreen-bug-fixing`, which contains the
Phase 8 action-planning work).

All changes are confined to `use-cases/campusgreen/`. No Agent Kernel core, framework, other use
case, or frozen docs were touched.

---

## 1. Repo & branch baseline

| Item | State |
| --- | --- |
| Branch | `feat/campusgreen-phase9a-audit` (created for this audit) |
| Parent branch | `feat/campusgreen-bug-fixing` @ `a89481a1` (Phase 8 action planning) |
| `origin/develop` | `acbb0f9b` — contains **none** of this use case (all 35 files exist only on the parent branch) |
| Remote | `dasunjlk/agent-kernel` (fork) |
| Working tree after changes | clean except intended edits (all under `use-cases/campusgreen/`) |
| Env vars present | none: no `OPENAI_API_KEY`, `GROQ_API_KEY`, or `AK_WHATSAPP__*` |
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
- `groq_readiness_test.py` (new): 6 deterministic offline checks (default path returns `None` and
  keeps OpenAI; key path returns a chat-completions model with the Groq model string and base URL;
  the client host is Groq, not `api.openai.com`; the seven tools are preserved) plus 1 live
  probe gated on `GROQ_API_KEY` + the Unix-only CLI. **No real Groq key exists in this
  environment, so the live probe is skipped — readiness is reported honestly.**
- `README.md` / `INTEGRATION.md`: new "LLM providers" documentation.

**Remaining (intentionally not changed):** the Tier-2 conversational tests keep using OpenAI's
Responses API, so they remain OpenAI-gated. Groq is an *alternative path*, not a replacement for
the OpenAI-backed test tier.

---

## 3. WhatsApp readiness

**Assessment: architecturally ready; not live-verifiable here.**

Verified offline (uses the real `AgentWhatsAppRequestHandler` via `object.__new__`, no
credentials):
- `whatsapp_handler.py` shim: message normalization (strip whitespace, drop empty) and duplicate
  `wamid` dedup (`_DEDUP_MAX=10000`), delegating everything else to the native handler.
- `server.py` requires `OPENAI_API_KEY`, `AK_WHATSAPP__ACCESS_TOKEN`, `AK_WHATSAPP__PHONE_NUMBER_ID`,
  `AK_WHATSAPP__VERIFY_TOKEN`; `validate_config` reports only names; clean `SystemExit(2)`.
- Routing via `RESTAPI.run([CampusGreenWhatsAppHandler()])`; `session_id = sender number`;
  `_acting_user()` maps `ak.acting_user_id` to `reported_by`; `CAMPUSGREEN_CHANNEL=whatsapp`
  stamps `source_channel`.
- `integration_test.py`, `flow_scenarios_test.py`, `e2e_scenario_test.py` prove routing, session
  isolation, the boundary shim, and full tool workflows offline.

**Honest caveat:** real end-to-end WhatsApp delivery cannot be reproduced in this credential-less
Windows sandbox (no `AK_WHATSAPP__*`, no `OPENAI_API_KEY`, no public HTTPS tunnel, no Unix
`readline`). A **live smoke test needs a keyed POSIX CI runner**. This is documented in
`INTEGRATION.md` ("Real-credential smoke test").

---

## 4. Security posture

Verified (no code change required):
- `security_test.py` enforces: no secrets in committed source, `.env` ignored + untracked,
  `.env.example` placeholders-only, structured/sanitized `ak.campusgreen.tools` logs (no
  free-text descriptions or key material, `error=` codes), `validate_config` reports names only,
  clean startup exit, and anti-injection / anti-fabrication input-boundary behavior.
- `agent.py` rules forbid inventing locations, IDs, tickets, notifications, or statistics; tools
  return `{"status": "ok|error"}` envelopes and the agent never claims success without one.
- **No committed secrets or key material found** across `use-cases/campusgreen/`.

**Defect fixed:** `README.md:126` misspelled the gating variable as `OPENAPI_API_KEY` (missing
"I"); corrected to `OPENAI_API_KEY` to match the real gate and `.env.example`.

---

## 5. Test coverage / gating inventory

Measured on this machine with `uv run pytest -q`: **287 total — 271 passed, 16 skipped**
(previously documented as 280 / 265 / 15; the +7 Δ is the new Groq file: 6 deterministic + 1
gated).

- **Tier 1 — deterministic (271), offline, no key/network:** `demo_test.py` (unit), `tool_test.py`,
  `agent_behavior_test.py`, `action_planning_test.py`, `conversational_flows_test.py`,
  `flow_scenarios_test.py`, `integration_test.py`, `security_test.py` (deterministic half),
  `e2e_scenario_test.py`, `data_integrity_test.py`, and `groq_readiness_test.py` (deterministic
  half). Driven by `CampusGreenDriver` / `FakeAgentService` over the **real** tools with isolated
  `CAMPUSGREEN_DATA_DIR`/re-seeded fixtures; never calls Meta/OpenAI.
- **Tier 2 — gated (16):** conversational agent tests + LLM probes + live Groq probe, skipped
  without `OPENAI_API_KEY`/`GROQ_API_KEY` and the Unix `readline` (not stock Windows).

**Minor hygiene fix:** `pytest-order` (needed for `@pytest.mark.order`) was only transitively
installed; it is now declared explicitly in the `dev` group of `pyproject.toml` (`uv.lock`
updated).

---

## 6. Action items executed

| # | File | Change | Type |
| --- | --- | --- | --- |
| 1 | `agent.py` | Env-gated Groq `OpenAIChatCompletionsModel` wiring (`_resolve_model`) | feat |
| 2 | `.env.example` | `GROQ_API_KEY`, `GROQ_MODEL` empty placeholders | chore |
| 3 | `groq_readiness_test.py` (new) | 6 deterministic + 1 gated live Groq probe | test |
| 4 | `README.md` | Fix `OPENAI_API_KEY` typo; add LLM-providers section; update test counts + layout | docs |
| 5 | `INTEGRATION.md` | Document Groq path, `GROQ_*` vars, real-credential smoke-test note | docs |
| 6 | `pyproject.toml` | Declare `pytest-order` explicitly | chore |
| 7 | `.venv` (local) | Rebuilt cleanly (see §7) — gitignored, not committed | env |

Nothing outside `use-cases/campusgreen/` was modified.

---

## 7. Findings fixed during the audit (the "missing things")

1. **Broken `uv run pytest` due to a polluted virtualenv.** The repo-local `.venv` was a
   mislabeled copy of another use case's environment (`pyvenv.cfg` set `prompt =
   waste-sorting-assistant`; its `agentkernel` packages resolved to `waste-sorting-assistant\.venv`,
   which lacked the `whatsapp` extra's `fastapi` dependency). As a result `uv run pytest` (the
   documented command in README/INTEGRATION/TEST_REPORT) failed integration/security tests with
   `ModuleNotFoundError: No module named 'fastapi'`, while `uv run python -m pytest` passed. The
   `.venv` was deleted and recreated cleanly with CPython 3.12.13 (`prompt = campusgreen`) and
   `uv sync --all-extras --dev`. After the rebuild, `uv run pytest -q` → **271 passed, 16 skipped**.
   `.venv` is gitignored, so this repair is environmental only.
2. **README `OPENAPI_API_KEY` typo** (see §4).
3. **Undeclared `pytest-order` dependency** (see §5).

---

## 8. Scope guardrails honored

- No edits outside `use-cases/campusgreen/` (explicit user requirement).
- No fabricated credentials; no Groq/WhatsApp network calls attempted without a key.
- No forcing Groq as the default provider; OpenAI-backed Tier-2 tests untouched.
- No `terraform`, no push/PR, no commit without explicit confirmation (repo rule).
- No changes to `docs/versioned_docs/` or Agent Kernel core.

---

## 9. Risks / open items for Phase 9B

- **Real Groq and WhatsApp paths are verifiable here only by inspection + deterministic tests.**
  Live confirmation requires a keyed POSIX CI runner (Groq key; Meta `AK_WHATSAPP__*` credentials
  + public HTTPS tunnel). Documented as the recommended Phase 9B smoke-test prerequisite.
- The rebuilt `.venv` is local; a fresh clone via `build.sh` (`uv venv && uv sync --all-extras
  --dev`) is the canonical way to reproduce it. Consider pinning `requires-python`/a `.python-version`
  to avoid a future default-interpreter drift to 3.14 (`uv venv --python 3.12.13` was used here to
  match the original 3.12 toolchain).
- Test counts in the docs now reflect the audited run (**287 / 271 / 16**); re-validate if the
  suite grows again.

---

## 10. Recommended Phase 9B guardrail & final report

**Groq wiring (as implemented, in-scope):** `_resolve_model()` in `agent.py` returns an explicit
`OpenAIChatCompletionsModel` (chat-completions, since Groq has no Responses API) pointed at
`https://api.groq.com/openai/v1` when `GROQ_API_KEY` is set, else `None` (OpenAI default
unchanged). No global forcing, no core/framework changes.

**Deliverable:** this audit report plus the seven changes in §6, all contained in
`use-cases/campusgreen/` on `feat/campusgreen-phase9a-audit`. Recommended next step: Phase 9B
testing campaign in a keyed POSIX CI environment to run the gated Tier-2 suite and the real
Groq/WhatsApp smoke tests.
