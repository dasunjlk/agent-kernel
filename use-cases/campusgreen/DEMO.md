# CampusGreen Demos

How to run each CampusGreen surface, what to expect, and how to reset the data.

CampusGreen has three runnable surfaces. All share the same agent (`agent.py`) and
the same six tools (`tool.py`); only the channel boundary differs.

| Surface | Command | Needs | Opens |
| --- | --- | --- | --- |
| CLI demo | `uv run python demo.py` | `OPENAI_API_KEY` + Unix `readline` (not stock Windows) | interactive CLI |
| Local WhatsApp demo | `uv run python integration_demo.py` | `OPENAI_API_KEY` only | prints a scripted WhatsApp exchange |
| Real WhatsApp server | `uv run python server.py` | Meta credentials + public HTTPS tunnel | WhatsApp webhooks |

## 1. CLI demo (`demo.py`)

```bash
./build.sh                  # setup (Python 3.12, uv, deps)
uv run python demo.py       # or: uv run python -m agentkernel.cli
```

The default agent is `campusgreen`. Try these prompts in order to see the full flow:

```text
There's a water leak outside Lab 3.
What's the status of WTR-001?
The bins near the Student Cafe are overflowing.
What are the biggest sustainability problems this month?
What's the current status?
```

Each report verifies the location, creates an issue, notifies the responsible team,
and can be followed up (status, escalation, re-notify).

> **Windows note:** `agentkernel.cli` imports the Unix-only `readline` module, so the
> CLI demo cannot start on stock Windows. Use the WhatsApp demo below — it exercises
> the exact same agent and tools.

## 2. Local WhatsApp demo (`integration_demo.py`) — no Meta needed

```bash
cp .env.example .env        # Windows: copy .env.example .env
# edit .env and set OPENAI_API_KEY

uv run python integration_demo.py
```

Prints a scripted exchange exactly as a real WhatsApp user would see it: a student
reports a leak (lookup → create → notify), the responsible team is picked up, a
status question is answered from stored data, a worsening leak is escalated, a second
sender's bins complaint runs in its own session, and partial failures are reported
truthfully. Requires **no** Meta account and no tunnel; the only external service is
the OpenAI API.

## 3. Real WhatsApp server (`server.py`)

```bash
cp .env.example .env        # then fill in the Meta variables + OPENAI_API_KEY
uv run python server.py
```

Needs `AK_WHATSAPP__ACCESS_TOKEN`, `AK_WHATSAPP__PHONE_NUMBER_ID`,
`AK_WHATSAPP__VERIFY_TOKEN` (+ optional `AK_WHATSAPP__APP_SECRET`) and a public
HTTPS URL (e.g. an ngrok/pinggy tunnel) registered as the webhook with the
`messages` field. Missing required variables produce a clear startup message naming
only what's missing (never values), surfaced instead of a stack trace.

See [`INTEGRATION.md`](INTEGRATION.md) for the full wiring and setup.

## Data & reset

All surfaces persist issues and notifications to `data/issues.json` as you use them.
To return to the seeded state:

```bash
git checkout -- data/issues.json
```

## Channel and data-directory config

With `CAMPUSGREEN_CHANNEL=whatsapp` and `CAMPUSGREEN_DATA_DIR` unset, issues created
over WhatsApp are labelled `source_channel: whatsapp`. Set
`CAMPUSGREEN_CHANNEL=cli` (or unset it) to label issues `cli` for parity with the
local demo.

## What the demos don't cover

The demos intentionally do not cover: multi-tenant access control, a web dashboard,
long-term analytics, or durable deployed hosting — all deferred (see `SPEC.md` and
`README.md` Scope).