# CampusGreen WhatsApp Integration

CampusGreen is channel-independent at the agent and tool layer and is served to
end users over WhatsApp through Agent Kernel's `AgentWhatsAppRequestHandler`.
Both the local CLI demo (`demo.py`) and the WhatsApp path share the **same**
`campusgreen` agent and tool logic in `agent.py` / `tool.py`; only the channel
boundary differs.

This document explains how the integration is wired, how to run it locally, and
how to test it without touching Meta or the OpenAI API.

## How it works

An incoming WhatsApp message arrives as a webhook to the REST API. CampusGreen's
thin boundary shim (`CampusGreenWhatsAppHandler`, see `whatsapp_handler.py`)
wraps Agent Kernel's `AgentWhatsAppRequestHandler` (a `RESTRequestHandler`): the
shim normalizes the message and guards against duplicate events, then hands the
message to the native handler, which parses it and routes it into the
`campusgreen` agent:

```text
Student (WhatsApp)
  -> Meta Cloud API webhook
  -> /whatsapp/webhook (CampusGreenWhatsAppHandler over AgentWhatsAppRequestHandler)
  -> [shim] normalize text + skip duplicate events
  -> AgentService.select(session_id=from_number, name="campusgreen")
  -> CampusGreen agent (agent.py) + tools (tool.py)
  -> AgentService.run_multi -> AgentReplyText
  -> handler._send_message -> WhatsApp back to the student
```

Two Agent Kernel mechanisms do the heavy lifting for us:

- **Per-sender session isolation** — the handler sets `session_id` to the sender's
  number, so each student gets their own ongoing CampusGreen conversation with no
  extra plumbing on our side.
- **Same agent + tools as the CLI** — the WhatsApp server registers the exact
  `AGENTS` from `agent.py` via `OpenAIModule`, so everything the CLI demo can do
  (report, lookup, create, notify, status, escalate, analytics) works over
  WhatsApp too.

Messages that aren't plain text or an image/document (e.g. audio/video) are
rejected at the channel boundary by the handler before the agent runs.

### Message normalization and duplicate events

The phase-6 responsibilities that the native handler does not provide live in
the CampusGreen shim (`whatsapp_handler.py`), which only pre-processes the
message and then delegates everything else:

- **Normalization.** Text bodies are stripped of surrounding whitespace, so the
  agent always receives a clean user message. A whitespace-only or empty text
  payload is ignored (`message_ignored empty_text`) instead of being passed
  through as a meaningless prompt.
- **Duplicate platform events.** WhatsApp can redeliver the same webhook event
  (with the same message `id`). A small in-memory set of processed message IDs
  skips an already-seen event (`duplicate_event_skipped`), so a redelivered
  `wamid` is not reprocessed into a second ticket. This is a deliberately simple
  per-process guard — not a durable or distributed idempotency layer — and it is
  keyed by the platform event ID, so two distinct user-sent messages with the
  same text still each become their own report.

## Files

| File | Role |
| --- | --- |
| `whatsapp_handler.py` | `CampusGreenWhatsAppHandler` — the thin CampusGreen boundary shim over `AgentWhatsAppRequestHandler`. Normalizes text (strips whitespace, drops empty) and skips duplicate platform events by message ID, then delegates to the native handler. |
| `server.py` | Real WhatsApp entry point. Loads `AGENTS`, registers `OpenAIModule`, and serves the shim (over `AgentWhatsAppRequestHandler`) via `RESTAPI.run`. Needs real Meta credentials + a public HTTPS tunnel. |
| `integration_demo.py` | Local demo driver. Subclasses `CampusGreenWhatsAppHandler` and overrides `_send_message` to print replies locally, so it exercises the **same** routing and boundary logic but needs **no** Meta credentials. Only needs `OPENAI_API_KEY`. |
| `integration_test.py` | Deterministic pytest suite for the routing/session/error boundary, the shim's normalization + duplicate-event handling, and the tool workflows end to end (see below). Uses an isolated copy of the seed data and never calls Meta or OpenAI. |
| `test_helpers.py` | Shared offline harness: `FakeAgentService` (scripted LLM stand-in), `CampusGreenDriver` (tool-calling driver), `new_handler`/`new_shim_handler` (credential-free handler construction), fixtures, and helpers used by the integration/behavior/e2e suites. |
| `conftest.py` | Shared isolated-data fixtures (`isolated_data_dir`, `isolated_store`) that re-seed a temp data dir for every test. |
| `config.yaml` | `whatsapp.agent: "campusgreen"` and the `api` host/port the server binds. |
| `.env.example` | Template for the secrets the server needs. |

## Run it locally (no Meta credentials)

```bash
# copy the template and add a real OpenAI key
cp .env.example .env        # Windows: copy .env.example .env
# edit .env and set OPENAI_API_KEY

uv run python integration_demo.py
```

This prints a scripted exchange — a student reports a leak, the agent verifies
the location, creates the issue, notifies the responsible team, answers a status
question, escalates a worsening condition, and a second sender's bins complaint
is handled in its own session. Because the handler intercepts the outbound
WhatsApp call, **no Meta account or public URL is required**; the only external
service is the OpenAI API the agent reasons with.

## Run the real server

The `server.py` path is what a deployed instance uses. It needs:

- Real Meta credentials: `AK_WHATSAPP__ACCESS_TOKEN`, `AK_WHATSAPP__PHONE_NUMBER_ID`,
  `AK_WHATSAPP__VERIFY_TOKEN` (and optionally `AK_WHATSAPP__APP_SECRET` for webhook
  signature verification).
- An `OPENAI_API_KEY` for the agent.
- A public HTTPS URL reachable by Meta (e.g. an ngrok/pinggy tunnel to the local
  port), because WhatsApp only sends webhooks to public HTTPS endpoints.

```bash
uv run python server.py
```

`server.py` validates the required variables at startup: if anything is missing it
prints **only the names** of the missing variables and exits `2`, instead of failing
deep inside the SDK. `validate_config(environ)` is importable so the server checks it
explicitly and tests assert no value ever leaks to the log.

Then register `https://<your-tunnel>/whatsapp/webhook` as the webhook URL in the
Meta app with the subscribed field `messages`, using the verify token from your
environment.

### Runtime environment variables

| Variable | Purpose |
| --- | --- |
| `AK_WHATSAPP__ACCESS_TOKEN` | Meta Graph API access token (server only). |
| `AK_WHATSAPP__PHONE_NUMBER_ID` | WhatsApp Business phone number ID (server only). |
| `AK_WHATSAPP__VERIFY_TOKEN` | Webhook verification token (server only). |
| `AK_WHATSAPP__APP_SECRET` | Optional — enables request-signature verification. |
| `OPENAI_API_KEY` | Lets the agent reason and call tools (demo and server). |
| `CAMPUSGREEN_DATA_DIR` | Optional — point the JSON data layer elsewhere. |
| `CAMPUSGREEN_CHANNEL` | Optional — stamped on issues as `source_channel` (default `cli`). |

## Testing

```bash
uv run pytest -q                 # full suite (see TEST_REPORT.md for counts)
uv run pytest integration_test.py -q
```

The whole suite is deterministic and offline — no Meta, no OpenAI — except the
LLM-gated conversational tests (see `TEST_REPORT.md`). Two things make that possible:

`integration_test.py` constructs the handler without its real `__init__` (so no
credentials are needed), replaces the outbound `_send_message` with an in-memory
recorder, and swaps the `AgentService` for a scripted stand-in. Three layers are covered:

- **Routing / session / errors**: an incoming text message reaches the service with
  the right prompt, `session_id` (= sender) and agent name; distinct senders get
  distinct sessions; the agent reply is sent back as a WhatsApp message; a missing
  agent and a raised error both map to friendly WhatsApp messages; non-text
  attachments (audio/video) are rejected at the boundary.
- **Shim normalization + duplicate events**: whitespace-only and empty text are
  ignored (no agent call, no reply); a text body is stripped before reaching the
  agent; a redelivered event with the same message ID is processed once and the
  repeat skipped; two distinct messages with the same text are both processed
  (dedup is by event ID, not content).
- **Tool workflows through the handler**: a deterministic stand-in for the LLM
  drives the **real** `tool.py` functions — report (lookup -> create -> notify),
  unknown location (no issue created), status (get_issue), escalation
  (update + notify), truthful tool-failure reporting, **partial failure**
  (created but not notified — the reply says so and the notification log is clean),
  **duplicate reports** (two distinct tickets, documented as out-of-SPEC), and
  **multi-turn status** (resolves the session's active issue).

The agent-behavior, tool, data-integrity, security, and 11-step e2e suites extend the
same harness: see `TEST_REPORT.md` for per-file coverage and `EVALUATION.md` for the
scenario matrix. The Phase 7 **polished user flows** are pinned by two additional
drive-the-handler suites: `conversational_flows_test.py` (the per-flow matrix —
multi-turn clarification, contextual status, multi-issue reference resolution by
topic, courtesy/no-tool and unsupported-request handling, and retry-after-failure)
and `flow_scenarios_test.py` (whole cohesive conversations replayed one prompt at a
time and cross-checked against the persisted `issues.json`). All tests run against an
isolated, re-seeded copy of the data (via `test_helpers`/`conftest` and `demo_test`'s
own temp-data fixture), so the committed `data/issues.json` is never mutated by the
suite.

## Known limitations

- Duplicate-event handling is a simple **per-process, in-memory** guard (a bounded
  set of message IDs). It is not durable or shared across server instances or
  restarts — a restart clears the set. WhatsApp rarely redelivers, so this covers
  the common case without a distributed idempotency layer.
- The `_seen_message_ids` set is bounded (10 000 entries) to avoid unbounded
  memory growth in a long-lived process.
- Non-text attachments (audio/video) are rejected by the native handler; images and
  documents are handled natively but are not part of CampusGreen's issue-reporting
  flow in this phase.
