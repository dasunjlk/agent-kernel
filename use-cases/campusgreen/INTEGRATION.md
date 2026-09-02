# CampusGreen Telegram Integration

CampusGreen is channel-independent at the agent and tool layer and is served to
end users over Telegram through Agent Kernel's `AgentTelegramRequestHandler`.
Both the local CLI demo (`demo.py`) and the Telegram path share the **same**
`campusgreen` agent and tool logic in `agent.py` / `tool.py`; only the channel
boundary differs.

This document explains how the integration is wired, how to run it locally, and
how to test it without touching Telegram servers or the OpenAI API.

## How it works

Incoming Telegram updates arrive via **long polling** (`getUpdates`) or as webhooks to
the REST API. CampusGreen's boundary shim (`CampusGreenTelegramHandler`, see
`telegram_handler.py`) wraps Agent Kernel's `AgentTelegramRequestHandler`: the
shim normalizes the message, handles `/start` and `/help` commands, guards against
duplicate update/message events, and routes messages into the `campusgreen` agent:

```text
Student (Telegram)
  -> Telegram Bot API
  -> getUpdates / Long Polling (CampusGreenTelegramHandler)
  -> [shim] normalize text + handle /start + skip duplicate events
  -> AgentService / ChatService.select(session_id=chat_id, name="campusgreen")
  -> CampusGreen agent (agent.py) + tools (tool.py)
  -> Agent reply
  -> handler._send_message -> Telegram back to the student
```

Two Agent Kernel mechanisms do the heavy lifting:

- **Per-chat session isolation** — the handler sets `session_id` to the Telegram
  `chat_id` (and `user_id` to `from.id`), so each student gets their own ongoing
  CampusGreen conversation with no extra plumbing on our side.
- **Same agent + tools as the CLI** — the Telegram server registers the exact
  `AGENTS` from `agent.py` via `OpenAIModule`, so everything the CLI demo can do
  (report, lookup, create, notify, status, escalate, analytics, action planning)
  works over Telegram too.

### Message normalization and duplicate events

The phase-9A responsibilities that the native handler does not provide live in
the CampusGreen shim (`telegram_handler.py`), which pre-processes the
message and delegates everything else:

- **Normalization.** Text bodies are stripped of surrounding whitespace, so the
  agent always receives a clean user message. A whitespace-only or empty text
  payload is ignored (`message_ignored empty_text`) instead of being passed
  through as a meaningless prompt.
- **Duplicate platform events.** Telegram updates can be redelivered. A small
  in-memory set of processed update and message IDs skips an already-seen event
  (`duplicate_update_skipped` / `duplicate_message_skipped`), so a redelivered
  event is not reprocessed into a second ticket. This is a simple per-process
  guard keyed by platform event ID.
- **/start command.** Introduces CampusGreen and outlines what the user can do
  (report issues, check status, request action plans) without invoking the agent.

## Files

| File | Role |
| --- | --- |
| `telegram_handler.py` | `CampusGreenTelegramHandler` — the thin CampusGreen boundary shim over `AgentTelegramRequestHandler`. Normalizes text, handles `/start` and `/help`, skips duplicate events by update/message ID, and provides `poll()` for local long polling. |
| `server.py` | Telegram entry point. Runs long polling by default (`uv run python server.py`) or starts the REST webhook server (`--webhook`). Validates `TELEGRAM_BOT_TOKEN`. |
| `integration_demo.py` | Local demo driver. Subclasses `CampusGreenTelegramHandler` and overrides `_send_message` to print replies locally, so it exercises the **same** routing and boundary logic with **no** Telegram credentials. Only needs `OPENAI_API_KEY` (or `GROQ_API_KEY`). |
| `integration_test.py` | Deterministic pytest suite for the routing/session/error boundary, the shim's normalization + duplicate-event handling, `/start` behavior, and the tool workflows end to end. Uses an isolated copy of the seed data and never calls Telegram or OpenAI. |
| `test_helpers.py` | Shared offline harness: `FakeAgentService` (scripted LLM stand-in), `CampusGreenDriver` (tool-calling driver), `new_handler`/`new_shim_handler` (credential-free handler construction), fixtures, and helpers. |
| `conftest.py` | Shared isolated-data fixtures (`isolated_data_dir`, `isolated_store`) that re-seed a temp data dir for every test. |
| `config.yaml` | `telegram.agent: "campusgreen"` and the `api` host/port the server binds. |
| `.env.example` | Template for the secrets the server needs. |

## Run it locally (no Telegram credentials)

```bash
# copy the template and add a real OpenAI key (or Groq key)
cp .env.example .env        # Windows: copy .env.example .env
# edit .env and set OPENAI_API_KEY (or GROQ_API_KEY)

uv run python integration_demo.py
```

This prints a scripted exchange — a student reports a leak, the agent verifies
the location, creates the issue, notifies the responsible team, answers a status
question, escalates a worsening condition, a second sender's bins complaint is
handled in its own session, and (as the closing turns) the agent produces an
evidence-grounded action plan, explains its ranking, and escalates a ticket only
when explicitly asked. Because the handler intercepts the outbound Telegram call,
**no Telegram bot token is required**; the only external service is the
LLM API the agent reasons with.

## Run the real server

For local development with a real Telegram bot, long polling is the default:

```bash
# In .env set:
# TELEGRAM_BOT_TOKEN=<your-bot-token-from-BotFather>
# OPENAI_API_KEY=<your-key> (or GROQ_API_KEY)

uv run python server.py
```

No ngrok, pinggy, or public HTTPS URL is needed for long polling.

To run as a webhook server mounted on the REST API instead:

```bash
uv run python server.py --webhook
```

`server.py` validates the required variables at startup: if anything is missing it
prints **only the names** of the missing variables and exits `2`.

### Runtime environment variables

| Variable | Purpose |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot token from @BotFather (server only). |
| `AK_TELEGRAM__BOT_TOKEN` | Alternative AK-prefixed Telegram Bot token. |
| `OPENAI_API_KEY` | Lets the agent reason and call tools (demo and server). |
| `GROQ_API_KEY` | Optional — route the agent through Groq (chat-completions only). See *LLM providers* below. |
| `GROQ_MODEL` | Optional — Groq model to use when `GROQ_API_KEY` is set (default `llama-3.3-70b-versatile`). |
| `CAMPUSGREEN_DATA_DIR` | Optional — point the JSON data layer elsewhere. |
| `CAMPUSGREEN_CHANNEL` | Optional — stamped on issues as `source_channel` (default `cli`, `telegram` for Telegram). |

### LLM providers

The Telegram path registers the same `AGENTS` from `agent.py`, so whichever model the agent
resolves applies to the server exactly as it does to the CLI. By default the OpenAI Agents SDK
serves OpenAI's Responses API. To run the server against **Groq** — which only serves
chat-completions — set `GROQ_API_KEY` (and optionally `GROQ_MODEL`); `agent.py` then builds an
explicit `OpenAIChatCompletionsModel` pointed at `https://api.groq.com/openai/v1`. Without the key
the OpenAI path is unchanged.

**Real-credential smoke test.** The deterministic suite proves routing, session isolation, the
boundary shim, and the tool workflows offline with no credentials. Live end-to-end Telegram
delivery requires a smoke test in an environment holding real `TELEGRAM_BOT_TOKEN` /
`OPENAI_API_KEY` (or `GROQ_API_KEY`) credentials.

## Testing

```bash
uv run pytest -q                 # full suite
uv run pytest integration_test.py -q
```

The whole suite is deterministic and offline — no Telegram, no OpenAI — except the
LLM-gated conversational tests.

`integration_test.py` constructs the handler without its real `__init__` (so no
credentials are needed), replaces the outbound `_send_message` with an in-memory
recorder, and swaps the agent service for a scripted stand-in.
