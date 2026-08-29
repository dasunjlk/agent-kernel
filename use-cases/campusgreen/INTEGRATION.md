# CampusGreen WhatsApp Integration

CampusGreen is channel-independent at the agent and tool layer and is served to
end users over WhatsApp through Agent Kernel's `AgentWhatsAppRequestHandler`.
Both the local CLI demo (`demo.py`) and the WhatsApp path share the **same**
`campusgreen` agent and tool logic in `agent.py` / `tool.py`; only the channel
boundary differs.

This document explains how the integration is wired, how to run it locally, and
how to test it without touching Meta or the OpenAI API.

## How it works

An incoming WhatsApp message arrives as a webhook to the REST API. Agent Kernel's
`AgentWhatsAppRequestHandler` (a `RESTRequestHandler`) parses the message and
routes it into the `campusgreen` agent:

```text
Student (WhatsApp)
  -> Meta Cloud API webhook
  -> /whatsapp/webhook (AgentWhatsAppRequestHandler)
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

## Files

| File | Role |
| --- | --- |
| `server.py` | Real WhatsApp entry point. Loads `AGENTS`, registers `OpenAIModule`, and serves the handler via `RESTAPI.run`. Needs real Meta credentials + a public HTTPS tunnel. |
| `integration_demo.py` | Local demo driver. Subclasses the handler and overrides `_send_message` to print replies locally, so it exercises the **same** routing but needs **no** Meta credentials. Only needs `OPENAI_API_KEY`. |
| `integration_test.py` | Deterministic pytest suite for the routing/session/error boundary and the tool workflows end to end (see below). Uses an isolated copy of the seed data and never calls Meta or OpenAI. |
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
uv run pytest integration_test.py -q
```

The integration tests are fully deterministic and offline. They construct the
handler without its real `__init__` (so no credentials are needed), replace the
outbound `_send_message` with an in-memory recorder, and swap the `AgentService`
the handler instantiates for a scripted stand-in. Two layers are covered:

- **Routing / session / errors**: an incoming text message reaches the service with
  the right prompt, `session_id` (= sender) and agent name; distinct senders get
  distinct sessions; the agent reply is sent back as a WhatsApp message; a missing
  agent and a raised error both map to friendly WhatsApp messages.
- **Tool workflows through the handler**: a deterministic stand-in for the LLM
  drives the **real** `tool.py` functions — report (lookup -> create -> notify),
  unknown location (no issue created), status (get_issue), escalation
  (update + notify), and truthful tool-failure reporting.

All tests run against an isolated copy of the seed data (via a `CAMPUSGREEN_DATA_DIR`
pointing at a temp dir) and never call Meta or the OpenAI API. `integration_test.py`
and the existing `demo_test.py` run together in one `pytest` invocation with no
interference, because each isolates its own global issue store.
