# CampusGreen: Guidance for AI Agents

If you are an AI coding assistant (like Claude Code, Cursor, Copilot, or Antigravity) working on the **CampusGreen** use-case, this document contains the critical context and rules you need to understand how the project is built. 

## 1. Model Preference
For this specific use-case, **you must configure the runtime to use the `openai/gpt-oss-20b` model** (or a compatible provider). This model was explicitly chosen because it provides the best performance and highest reliability for strict tool calling. 

## 2. Architecture & File Map
CampusGreen is an Agent Kernel application that serves a Telegram bot.
- `agent.py`: Contains the system instructions and binds the python tools to the Agent Kernel framework.
- `tool.py`: Contains the core logic for all 7 tools (e.g., `create_issue`, `update_issue`, `lookup_campus_location`).
- `telegram_handler.py` & `server.py`: Handles the Telegram long-polling API and formats Agent markdown (like `**bold**`) into Telegram-compatible HTML.
- `data/*.json`: The local "database". 
- `integration_test.py` & `test_helpers.py`: The test suite. `test_helpers.py` actually mocks the LLM responses, so if you change how tools work, you *must* update the mock logic in `test_helpers.py`.

## 3. Critical Design Patterns & Constraints
Before writing any code, adhere to these strict rules:

### A. Strict Tool Typing (Groq Validation)
Do **not** use `str | None` or `Optional[str]` in tool function signatures in `tool.py`. The LLM endpoint has strict JSON schema validation. If a tool parameter can be omitted, give it a default value (e.g., `parameter: str = ""`) rather than a union type, otherwise the model will throw an `Invalid model or resource not found` (HTTP 400 BadRequest) error.

### B. No External Databases
All state is stored locally in `data/issues.json`, `data/locations.json`, etc. Do **not** attempt to install SQLite, PostgreSQL, or any ORM. The architecture intentionally uses simple JSON file I/O for state management.

### C. Duplicate Issue Handling
There is explicit duplicate detection inside the `create_issue` tool. If a user reports an issue for a category and location that already has an `OPEN` or `REPORTED` issue, `create_issue` will return a `duplicate_issue_exists` error back to the LLM. 
- The LLM is instructed to catch this error, thank the user, and politely inform them that the issue is already known.
- Do not bypass this logic. 
- When writing tests for new issues, use completely fresh locations (e.g., "East Walkway") to avoid colliding with seed data in `locations.json` like "Lab 3".

### D. Location Resolution
Users speak in natural language (e.g., "the bathroom on the second floor"). The agent **must** use `lookup_campus_location` to match this against `data/locations.json` to get a valid `location_id`. Do not allow the agent to blindly invent location IDs.

### E. Telegram Commands
Custom Telegram commands (like `/start` and others) are processed in `telegram_handler.py` via the `_handle_command` method. When adding new slash-commands, ensure they are routed and handled there.

### F. Environment Variables
Never hardcode API keys. The application expects `TELEGRAM_BOT_TOKEN` and an LLM key (like `OPENAI_API_KEY` or `GROQ_API_KEY`) to be provided in `.env` or via environment variables.

### G. Troubleshooting SSL Handshake Failures
If the user encounters an `SSLV3_ALERT_HANDSHAKE_FAILURE` or `httpcore.ConnectError` while running `server.py`, **do not modify the python code**. This specifically indicates that the user's local network (ISP, VPN, or Antivirus Web Shield like Kaspersky/ESET) is actively intercepting and dropping the connection to `api.telegram.org`. Instruct the user to disable their VPN or Antivirus shield.
