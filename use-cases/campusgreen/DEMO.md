# CampusGreen Demos

How to run each CampusGreen surface, what to expect, and how to reset the data.

CampusGreen has three runnable surfaces. All share the same agent (`agent.py`) and
the same seven tools (`tool.py`); only the channel boundary differs.

| Surface | Command | Needs | Opens |
| --- | --- | --- | --- |
| CLI demo | `uv run python demo.py` | `OPENAI_API_KEY` + Unix `readline` (not stock Windows) | interactive CLI |
| Local Telegram demo | `uv run python integration_demo.py` | `OPENAI_API_KEY` (or `GROQ_API_KEY`) only | prints a scripted Telegram exchange |
| Real Telegram server | `uv run python server.py` | `TELEGRAM_BOT_TOKEN` + `OPENAI_API_KEY` (or `GROQ_API_KEY`) | Telegram bot (long polling) |

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
What should we prioritize to improve sustainability this month?
Why is ENERGY ranked first?
Escalate the top unresolved energy issue.
What's the current status?
```

Each report verifies the location, creates an issue, notifies the responsible team,
and can be followed up (status, escalation, re-notify). The planning turns show the
agent prioritizing the recorded issues with evidence and only acting (escalating)
when explicitly asked.

> **Windows note:** `agentkernel.cli` imports the Unix-only `readline` module, so the
> CLI demo cannot start on stock Windows. Use the Telegram demo below — it exercises
> the exact same agent and tools.

## 2. Local Telegram demo (`integration_demo.py`) — no Telegram token needed

```bash
cp .env.example .env        # Windows: copy .env.example .env
# edit .env and set OPENAI_API_KEY (or GROQ_API_KEY)

uv run python integration_demo.py
```

Prints a scripted exchange exactly as a real Telegram user would see it: a student
sends `/start`, reports a leak (lookup → create → notify), the responsible team is picked up, a
status question is answered from stored data, a worsening leak is escalated, a second
sender's bins complaint runs in its own session, partial failures are reported
truthfully, and — as the final turns — the agent produces an evidence-grounded
action plan, explains its ranking, and escalates a ticket only when explicitly asked.
Requires **no** Telegram bot token; the only external service is the LLM
API.

## 3. Real Telegram server (`server.py`)

```bash
cp .env.example .env        # then fill in TELEGRAM_BOT_TOKEN + OPENAI_API_KEY / GROQ_API_KEY
uv run python server.py
```

Runs **long polling** against the Telegram Bot API by default (no ngrok, pinggy, or public HTTPS URL
needed). To run as a webhook server mounted on the REST API instead: `uv run python server.py --webhook`.
Missing required variables produce a clear startup message naming only what's missing (never values).

See [`INTEGRATION.md`](INTEGRATION.md) for the full wiring and setup.

## 4. Polished user flows — one coherent conversation

CampusGreen is built to feel like a single natural conversation, not a menu of
one-shot commands. The same session carries context across turns: a follow-up
continues the in-progress task, status questions resolve by topic, and courteous
or out-of-scope messages are handled conversationally without unnecessary tool
calls. An example, as a student would experience it on Telegram:

```text
Student: /start
CampusGreen: 👋 Welcome to CampusGreen! I'm your campus sustainability assistant…

Student: There is a leak.
CampusGreen: I can help with that. Which campus building, room, or landmark is it near?
Student: Near Lab 3.
CampusGreen: Water issue reported. Ticket: WTR-012 … The Facilities Zone B team has been notified.

Student: The bins near the Student Cafe are overflowing.
CampusGreen: Waste issue reported. Ticket: WST-007 … The Catering Services team has been notified.

Student: What is the status of the leak?
CampusGreen: Ticket: WTR-012 — Status: REPORTED, Priority: HIGH, Assigned team: Facilities Zone B.
Student: It is getting worse and spreading.
CampusGreen: Ticket WTR-012 escalated to Critical. The Facilities Zone B team has been notified.

Student: Thanks!
CampusGreen: You're welcome! I'm here if you need to report an issue or check on one.
Student: Can you book me a university bus?
CampusGreen: That's outside what I can do. I can report and track sustainability issues…
```

Each turn is answered the same way the real product would be, and nothing is ever
fabricated: every claim about a ticket, team, status, or notification comes from a
tool result, and polite acknowledgements and refusals make no tool call at all.

Action planning is a conversation of its own, grounded in the recorded issues and
acted on only when asked:

```text
Student: What should we prioritize to improve sustainability this month?
CampusGreen: Here are the current sustainability priorities, based on the issues
             recorded this month:
             1. ENERGY — Evidence: 3 recorded issue(s) this month, 3 still open.
                ENE-002 (HIGH, IN_PROGRESS, Solar Array). Recommended action: …
Student: Why is ENERGY ranked first?
CampusGreen: ENERGY is the top priority because it has the most recorded issues this
             period (3 report(s)), and 3 of them are still open.
Student: Escalate the top unresolved energy issue.
CampusGreen: Escalated ENE-002 to Critical (IN_PROGRESS → ESCALATED). The Energy
             Management team has been notified.
```

The plan puts the counts and the real ticket list above the recommendation, and the
agent takes no operational action (no escalations, no notifications) inside the plan
itself — it waits for the explicit escalation request.

## 5. Data & reset

All surfaces persist issues and notifications to `data/issues.json` as you use them.
To return to the seeded state:

```bash
git checkout -- data/issues.json
```

## 6. Channel and data-directory config

With `CAMPUSGREEN_CHANNEL=telegram` and `CAMPUSGREEN_DATA_DIR` unset, issues created
over Telegram are labelled `source_channel: telegram`. Set
`CAMPUSGREEN_CHANNEL=cli` (or unset it) to label issues `cli` for parity with the
local demo.

## 7. What the demos don't cover

The demos intentionally do not cover: multi-tenant access control, a web dashboard,
long-term analytics, or durable deployed hosting — all deferred (see `SPEC.md` and
`README.md` Scope).