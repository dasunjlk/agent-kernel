# CampusGreen Specification

## 1. Project Overview

CampusGreen is a university sustainability coordinator built with Agent Kernel. It receives natural-language campus sustainability reports, classifies and prioritizes them, creates trackable issues, notifies the responsible team, remembers issue state across follow-up messages, and produces simple sustainability insights from recorded incidents.

Full name: CampusGreen - University Sustainability Coordinator.

Category: Open Category / Agentic AI.

Primary SDG: SDG 11 - Sustainable Cities and Communities.

Supporting SDGs:

- SDG 6 - Clean Water and Sanitation.
- SDG 7 - Affordable and Clean Energy.
- SDG 12 - Responsible Consumption and Production.

The Phase 1 scope is product specification only. Implementation files, runtime configuration, tests, and deployment assets are intentionally deferred until later phases.

## 2. Problem Statement

Universities continuously experience small but important sustainability and infrastructure issues, such as water leaks, unnecessary electricity use, overflowing bins, food waste, pollution, and damaged sustainability infrastructure.

These issues are often reported informally through messages, calls, or word of mouth. That process creates several problems:

- Reports may not reach the responsible team.
- Responsibility may be unclear.
- Priority may be decided inconsistently.
- Status may not be tracked.
- Follow-up messages may lose context.
- Individual reports are not converted into campus-wide sustainability intelligence.

CampusGreen addresses the gap between noticing a sustainability issue and coordinating a tracked response.

## 3. Goals

- Provide a natural-language interface for students, staff, and facilities coordinators to report sustainability issues.
- Classify reported issues into a controlled sustainability category.
- Identify known campus locations from user messages.
- Determine an appropriate priority level from the reported situation.
- Create a trackable issue with an ID, status, category, location, priority, and assigned team.
- Notify the responsible team through a tool-mediated action.
- Remember recent issue context within a session so follow-up messages can update the correct issue.
- Retrieve existing issue status when users ask for updates.
- Generate summary insights about common sustainability issues over a defined period.
- Demonstrate meaningful Agent Kernel usage through an agent, tools, state, a local demo, tests, deployment, and a messaging integration.

## 4. Non-Goals

- Do not build a general university chatbot.
- Do not provide official emergency response or safety dispatch.
- Do not connect to real university maintenance systems in the first version.
- Do not require live IoT sensors, GPS data, camera input, or real-time energy meters.
- Do not implement predictive maintenance in the first version.
- Do not allow the agent to modify arbitrary files, databases, or external services outside the defined tools.
- Do not support every possible campus workflow; focus on sustainability issue coordination.

## 5. User Personas

### Student Reporter

A student notices an issue while moving around campus and wants to report it quickly through a familiar messaging interface.

Needs:

- Minimal form-filling.
- Clear confirmation that the issue was recorded.
- A ticket ID for follow-up.
- Confidence that the responsible team was notified.

### Staff Reporter

A lecturer, lab assistant, or administrative staff member reports recurring building or resource-waste issues.

Needs:

- Fast reporting from natural language.
- Ability to add extra details later.
- Status visibility for reported issues.

### Facilities Coordinator

A member of a facilities, maintenance, cleaning, or sustainability team receives issue notifications and updates status.

Needs:

- Clear location, category, priority, and description.
- Assigned team visibility.
- Escalation information when a report worsens.

### Sustainability Officer

A university sustainability stakeholder wants a simple summary of recurring campus problems.

Needs:

- Monthly or weekly issue summaries.
- Category-level counts.
- Identification of high-impact areas.
- Reliable data that comes from recorded issues, not fabricated statistics.

## 6. User Scenarios

### Scenario 1 - Report an Issue

User input:

```text
There is a water leak outside Lab 3.
```

Expected behavior:

- Understand that the user is reporting a new issue.
- Classify the issue as `WATER`.
- Identify `Lab 3` as the location.
- Look up the location in campus data.
- Determine priority as `HIGH` because active water loss is occurring.
- Create an issue.
- Assign the responsible team.
- Notify that team.
- Store the new issue ID in session state.

Expected response:

```text
Water leak reported.

Ticket: WTR-001
Location: Lab 3
Priority: High
Assigned team: Facilities Zone B
Status: Reported

The facilities team has been notified.
```

### Scenario 2 - Follow Up On An Issue

User input after Scenario 1:

```text
It is getting worse and water is spreading across the floor.
```

Expected behavior:

- Use session memory to resolve `it` to the most recent water leak issue.
- Retrieve the existing issue.
- Escalate priority from `HIGH` to `CRITICAL`.
- Update the issue description with the new information.
- Notify the responsible team again.

Expected response:

```text
Ticket WTR-001 has been escalated to Critical.

The report was updated because the leak is now spreading across the floor.
The responsible facilities team has been notified.
```

### Scenario 3 - Check Issue Status

User input:

```text
What is happening with the Lab 3 leak?
```

Expected behavior:

- Find the relevant issue by location and category.
- Retrieve the current issue state.
- Return a concise status summary.

Expected response:

```text
Ticket: WTR-001
Status: In Progress
Priority: Critical
Assigned team: Facilities Zone B
```

### Scenario 4 - Sustainability Insights

User input:

```text
What are the biggest sustainability problems this month?
```

Expected behavior:

- Call the sustainability reporting tool.
- Retrieve issue counts for the requested period.
- Rank the largest issue categories.
- Generate a natural-language insight based only on tool data.

Expected response:

```text
Top sustainability issues this month:

1. Energy - 41 incidents
2. Waste - 31 incidents
3. Water - 23 incidents

Energy-related reports are currently the largest sustainability concern on campus.
```

### Scenario 5 - Missing Information

User input:

```text
There is a problem.
```

Expected behavior:

- Detect that the issue type and location are missing.
- Ask a clarifying question.
- Do not create a ticket.

Expected response:

```text
I can help report it. What type of problem are you seeing, and where is it located?
```

### Scenario 6 - Unknown Location

User input:

```text
There is a leak near the old building.
```

Expected behavior:

- Attempt location lookup.
- Detect that the location is not known.
- Ask for a known building, room, zone, or nearby landmark.
- Do not create a ticket.

Expected response:

```text
I could not identify that campus location.

Could you provide the building name, room number, or a nearby known landmark?
```

## 7. Agent Responsibilities

CampusGreen must act as a coordinator, not only as a question-answering chatbot.

The agent is responsible for:

- Interpreting natural-language sustainability reports.
- Distinguishing new issue reports from follow-ups, status requests, and analytics questions.
- Classifying issues into one of the supported categories.
- Extracting or requesting a campus location.
- Assessing priority from the available context.
- Selecting the correct tool or tool sequence.
- Creating, retrieving, and updating issue records through tools.
- Notifying the responsible team through a tool.
- Remembering the active issue context during a session.
- Explaining outcomes clearly to the user.
- Avoiding claims that were not confirmed by tool results.

## 8. Agent Boundaries

The agent can:

- Interpret sustainability-related campus reports.
- Classify sustainability issue category and priority.
- Look up known campus locations.
- Create issue records through the issue tool.
- Retrieve issue records through the issue tool.
- Update issue records through the issue tool.
- Notify responsible teams through the notification tool.
- Analyze stored issue data through the reporting tool.
- Use session memory to resolve follow-up references.

The agent cannot:

- Invent campus locations that do not exist in the data.
- Claim a ticket was created if `create_issue` failed.
- Claim a team was notified if `notify_team` failed.
- Fabricate sustainability statistics.
- Directly mutate data outside the defined tools.
- Perform emergency dispatch.
- Promise a repair time unless a tool result provides one.
- Make policy decisions on behalf of the university.

## 9. Tool Specifications

### `lookup_campus_location`

Purpose: Resolve a user-provided place name to structured campus location data.

Input:

- `query`: Natural-language location text from the user.

Output on success:

- `location_id`
- `display_name`
- `building`
- `zone`
- `responsible_team_id`
- `aliases`

Failure behavior:

- Return a not-found result when no known location matches.
- The agent must ask for clarification and must not create an issue without a known location.

### `create_issue`

Purpose: Create a new sustainability issue record.

Input:

- `category`
- `location_id`
- `description`
- `priority`
- `reported_by`
- `source_channel`

Output on success:

- `issue_id`
- `status`
- `assigned_team_id`
- `created_at`

Failure behavior:

- Return an error result if the issue cannot be created.
- The agent must tell the user that the issue was not created and must not claim success.

### `get_issue`

Purpose: Retrieve an issue by ID or by search criteria.

Input:

- `issue_id`, or
- `category`
- `location_id`
- `status`
- `reported_by`

Output on success:

- `issue_id`
- `category`
- `location`
- `description`
- `priority`
- `status`
- `assigned_team`
- `history`

Failure behavior:

- Return not found when no matching issue exists.
- The agent must say it could not find the issue and ask for more details if needed.

### `update_issue`

Purpose: Update the status, priority, description, or history of an existing issue.

Input:

- `issue_id`
- Optional `status`
- Optional `priority`
- Optional `additional_note`
- Optional `resolution_note`

Output on success:

- `issue_id`
- `status`
- `priority`
- `updated_at`
- `history_entry`

Failure behavior:

- Return an error result if the issue cannot be updated.
- The agent must not claim escalation, resolution, or update success unless the tool confirms it.

### `notify_team`

Purpose: Notify the responsible team about a new, escalated, or updated issue.

Input:

- `team_id`
- `issue_id`
- `message`
- `notification_type`

Output on success:

- `notification_id`
- `team_id`
- `delivered`
- `delivered_at`

Failure behavior:

- Return an error result if notification fails.
- The agent must tell the user that the issue was recorded but notification failed, when applicable.

### `get_sustainability_report`

Purpose: Summarize recorded sustainability issues for a requested period.

Input:

- `period`
- Optional `category`
- Optional `location_id`

Output on success:

- `period`
- `category_counts`
- `priority_counts`
- `open_issue_count`
- `top_locations`
- `notable_trends`

Failure behavior:

- Return an error result if reporting data is unavailable.
- The agent must not fabricate statistics and must tell the user that the report is unavailable.

## 10. Data Model

The prototype may use local structured data files. Live university systems are not required for the first version.

Recommended data folder:

```text
data/
|-- locations.json
|-- teams.json
|-- issues.json
`-- sustainability.json
```

### Location

Fields:

- `location_id`
- `display_name`
- `building`
- `zone`
- `aliases`
- `responsible_team_id`

The prototype should include 10 to 20 locations so the demo is not hard-coded around a single room.

Example:

```json
{
  "location_id": "loc_lab_3",
  "display_name": "Lab 3",
  "building": "Engineering Block",
  "zone": "Zone B",
  "aliases": ["lab three", "engineering lab 3"],
  "responsible_team_id": "team_facilities_zone_b"
}
```

### Team

Fields:

- `team_id`
- `name`
- `responsibility`
- `zone`
- `contact_channel`

Example:

```json
{
  "team_id": "team_facilities_zone_b",
  "name": "Facilities Zone B",
  "responsibility": "Building maintenance, plumbing, lighting, and infrastructure",
  "zone": "Zone B",
  "contact_channel": "mock://facilities-zone-b"
}
```

### Issue

Fields:

- `issue_id`
- `category`
- `location_id`
- `description`
- `priority`
- `status`
- `assigned_team_id`
- `reported_by`
- `source_channel`
- `created_at`
- `updated_at`
- `history`

Example:

```json
{
  "issue_id": "WTR-001",
  "category": "WATER",
  "location_id": "loc_lab_3",
  "description": "Water leak outside Lab 3",
  "priority": "HIGH",
  "status": "REPORTED",
  "assigned_team_id": "team_facilities_zone_b",
  "reported_by": "student-demo-1",
  "source_channel": "cli",
  "created_at": "2026-08-28T10:00:00Z",
  "updated_at": "2026-08-28T10:00:00Z",
  "history": [
    {
      "timestamp": "2026-08-28T10:00:00Z",
      "event": "created",
      "note": "Initial report created from user message."
    }
  ]
}
```

### Sustainability Summary

Fields:

- `period`
- `category_counts`
- `priority_counts`
- `top_locations`
- `notable_trends`

The reporting tool should derive summaries from issue records when possible. If static demo data is used, the agent must treat it as tool-provided data and must not invent additional numbers.

## 11. Issue Categories

Supported categories for V1:

- `WATER`: Leaks, broken taps, water wastage, drainage problems.
- `ENERGY`: Lights, AC units, fans, machines, or equipment wasting electricity.
- `WASTE`: Overflowing bins, missing bins, recycling collection, litter.
- `FOOD`: Cafeteria food waste or avoidable food disposal.
- `POLLUTION`: Smoke, chemical smells, contaminated water, or visible pollution.
- `INFRASTRUCTURE`: Broken sustainability infrastructure such as solar panels, bins, signs, taps, or meters.
- `OTHER`: Sustainability-related issue that does not fit another category.

The agent must choose `OTHER` only when the report is sustainability-related but does not match a more specific category.

## 12. Priority Levels

Supported priority levels:

- `LOW`: General suggestion or minor issue with limited immediate impact.
- `MEDIUM`: Issue should be addressed but is not immediately harmful or severe.
- `HIGH`: Active resource loss, recurring waste, or significant operational impact.
- `CRITICAL`: Immediate safety risk, major infrastructure damage, severe resource loss, or rapid worsening.

Priority policy examples:

- A dripping tap with no flooding: `MEDIUM`.
- A visible water leak causing continuous water loss: `HIGH`.
- A leak spreading across a walkway or lab floor: `CRITICAL`.
- Lights left on in an unused room: `MEDIUM`.
- A whole building wing with unnecessary after-hours electricity use: `HIGH`.
- Overflowing waste bin in a low-traffic area: `MEDIUM`.
- Waste blocking a walkway or creating a hygiene risk: `HIGH`.

## 13. Issue Lifecycle

Issues use the following lifecycle:

```text
REPORTED
ASSIGNED
IN_PROGRESS
ESCALATED
RESOLVED
CLOSED
```

Status definitions:

- `REPORTED`: Issue was created but no team has accepted work yet.
- `ASSIGNED`: A responsible team has been identified or notified.
- `IN_PROGRESS`: The responsible team has started working on the issue.
- `ESCALATED`: Priority or urgency increased after new information.
- `RESOLVED`: The issue was fixed or handled.
- `CLOSED`: The issue is complete and no more action is expected.

Rules:

- New issues start as `REPORTED`.
- Successful team notification may move an issue to `ASSIGNED`.
- Follow-up messages may update the priority and append history.
- Escalation should set status to `ESCALATED` unless the issue is already `RESOLVED` or `CLOSED`.
- The agent must not mark an issue `RESOLVED` or `CLOSED` without a user message or tool result indicating completion.

## 14. Memory / State

CampusGreen must use Agent Kernel session state to preserve conversational context.

Session memory should store:

- Most recent issue ID for the session.
- Recently mentioned location.
- Recently mentioned category.
- Recent priority and status.
- Reporter identity when available from the channel.
- Last successful tool action.

Memory behavior:

- Follow-up phrases like `it`, `that leak`, or `the Lab 3 issue` should resolve to a known recent issue when the context is unambiguous.
- If multiple recent issues could match a follow-up, the agent must ask which issue the user means.
- Memory must not override tool truth. If memory says an issue exists but `get_issue` cannot retrieve it, the agent must report that mismatch instead of inventing state.
- Memory should be scoped to the user's session, not shared globally across unrelated users.

## 15. Agent Workflow

### New Issue Workflow

```text
User report
  -> Agent interprets intent
  -> Agent extracts category, location, and description
  -> lookup_campus_location
  -> Agent determines priority
  -> create_issue
  -> notify_team
  -> Agent stores active issue context
  -> Agent confirms result to user
```

### Follow-Up Workflow

```text
User follow-up
  -> Agent resolves referenced issue from session memory
  -> get_issue
  -> Agent decides whether status, priority, or notes should change
  -> update_issue
  -> notify_team when escalation or important update occurs
  -> Agent confirms update to user
```

### Status Workflow

```text
User asks for status
  -> Agent identifies issue from issue ID, location, category, or memory
  -> get_issue
  -> Agent summarizes current state
```

### Insight Workflow

```text
User asks for sustainability summary
  -> Agent identifies time period and filters
  -> get_sustainability_report
  -> Agent ranks tool-provided data
  -> Agent explains the insight without inventing statistics
```

## 16. Messaging Integration

CampusGreen is channel-independent at the agent and tool layer.

Required interfaces for the full MVP:

- Local CLI or demo script for development and judging.
- One Agent Kernel-supported messaging integration for user-facing operation, preferably WhatsApp if credentials are available, otherwise Slack.

Preferred flow:

```text
User
  -> WhatsApp or Slack
  -> Agent Kernel messaging integration
  -> CampusGreen agent
  -> CampusGreen tools
  -> State and data
  -> User response
```

Implementation expectations:

- The local demo and messaging integration must use the same agent and tool logic.
- Messaging-specific parsing should stay at the channel boundary.
- The core CampusGreen tools must not depend on WhatsApp or Slack-specific objects.
- If messaging credentials are unavailable, the local demo must still show the complete agentic workflow.

### Status (Phase 4)

WhatsApp is implemented as the user-facing messaging integration, served through Agent Kernel's
native `AgentWhatsAppRequestHandler` (the same handler that backs the `ak-py` examples API).

- `server.py` mounts the handler via `RESTAPI.run` and serves real Meta webhooks at
  `/whatsapp/webhook`; the handler routes each message into the `campusgreen` agent with
  `session_id = sender number` (per-user session isolation), and the agent's reply is sent back
  through WhatsApp.
- `integration_demo.py` reuses the identical routing but overrides the handler's `_send_message`
  to print locally, so the complete agentic workflow is demonstrable with **no Meta credentials**
  and no public tunnel; an `OPENAI_API_KEY` is the only external dependency.
- `integration_test.py` verifies the boundary offline: message routing, session isolation between
  senders, reply delivery, missing-agent and runtime-error mapping, unsupported-message rejection,
  and the full tool workflows (report, unknown location, status, escalation, truthful failure).
- `config.yaml` sets `whatsapp.agent: "campusgreen"`; credentials are environment-driven
  (`AK_WHATSAPP__*`). See `INTEGRATION.md` and `README.md` for setup and runtime details.
- If Meta credentials are unavailable, the local CLI demo (`demo.py`) and the local
  WhatsApp demo (`integration_demo.py`) both still show the complete workflow, satisfying the
  last expectation above.

## 17. Error Handling

### Unknown Location

If `lookup_campus_location` cannot resolve the location:

- Do not create an issue.
- Ask the user for a known building, room, zone, or landmark.
- Keep the partial report in session memory only as pending context.

### Missing Issue Type

If the user provides a location but no issue type:

- Ask what type of problem they are seeing.
- Do not create a ticket until the issue is clear enough to classify.

### Missing Location

If the user provides an issue but no location:

- Ask where the issue is located.
- Do not create a ticket.

### Ambiguous Follow-Up

If a follow-up could refer to multiple issues:

- Ask the user to choose the issue.
- Provide short candidates when available.

### Issue Creation Failure

If `create_issue` fails:

- Tell the user that the maintenance request could not be created.
- Do not notify a team.
- Do not store a fake active issue ID.

Required response pattern:

```text
I could not create the maintenance request right now. Please try again.
```

### Notification Failure

If `create_issue` succeeds but `notify_team` fails:

- Tell the user that the issue was recorded.
- Tell the user that team notification failed.
- Preserve the created issue ID.

Required response pattern:

```text
Ticket WTR-001 was created, but I could not notify the responsible team right now.
```

### Reporting Failure

If `get_sustainability_report` fails:

- Tell the user that the report is unavailable.
- Do not fabricate counts or trends.

## 18. Security

- The agent must only perform actions through explicitly defined tools.
- Tool inputs must be validated before writing records or sending notifications.
- User-provided text must be treated as untrusted input.
- The agent must not expose internal configuration, secrets, API keys, or messaging credentials.
- The agent must not reveal private reporter identifiers in public summaries.
- Data used for the prototype should be synthetic or dummy data unless the team has explicit permission to use real campus data.
- Notification tools should use mock or demo delivery channels until real team contacts are approved.
- The messaging integration must rely on Agent Kernel-supported request handling rather than custom unauthenticated public endpoints.

## 19. Testing Requirements

Later implementation phases must include tests for:

- Location lookup success and unknown-location failure.
- Issue classification for all V1 categories.
- Priority assessment for low, medium, high, and critical examples.
- Successful issue creation.
- Issue creation failure with no false success message.
- Successful follow-up update using session memory.
- Ambiguous follow-up handling.
- Notification success.
- Notification failure after issue creation.
- Issue status retrieval.
- Sustainability report generation from tool-provided data.
- Reporting failure with no fabricated statistics.
- Local CLI or demo flow for the four primary scenarios.

Tests should include both direct tool tests and conversational agent tests. Conversational tests that depend on prior turns should preserve one session ID across the ordered interaction.

## 20. Deployment Requirements

The MVP should support:

- Local execution through a `demo.py` or equivalent local entry point.
- Shared agent and tool code between local and deployed execution.
- A messaging integration through an Agent Kernel-supported channel.
- Environment-based configuration for secrets and runtime options.
- A deployable path using the repository's Agent Kernel patterns.

The first implementation may use local JSON data for the demo. If deployed, issue state must be backed by durable storage or an Agent Kernel session store suitable for the selected deployment target.

The implementation should document:

- How to install dependencies.
- How to run the local demo.
- How to run tests.
- How to configure the selected messaging channel.
- How to deploy or demonstrate the project.

## 21. SDG Alignment

### SDG 11 - Sustainable Cities and Communities

CampusGreen helps coordinate campus infrastructure and sustainability issues in a structured way. A university campus is a small city-like environment where better reporting, tracking, and resolution workflows can improve sustainability and livability.

### SDG 6 - Clean Water and Sanitation

Water leak reporting and escalation help reduce water waste and support cleaner facilities.

### SDG 7 - Affordable and Clean Energy

Energy-waste reporting helps identify unnecessary electricity consumption, such as lights, fans, AC units, or equipment left on when not needed.

### SDG 12 - Responsible Consumption and Production

Waste, recycling, and food-waste reports help the university identify recurring consumption and disposal problems.

## 22. MVP Scope

The MVP must include:

- Agent Kernel integration.
- One CampusGreen coordinator agent.
- Natural-language input.
- Issue classification.
- Priority assessment.
- Campus location lookup.
- Issue creation.
- Issue retrieval.
- Issue updates.
- Team notification through a tool.
- Session/state handling.
- Sustainability report generation.
- Local demo.
- One messaging integration, preferably WhatsApp or Slack.
- Tests.
- README.
- SPEC.
- Deployment path or clearly documented deployable structure.

The MVP should start with one strong coordinator agent plus tools. Additional specialized agents should be added only if they solve a demonstrated implementation problem.

## 23. Future Roadmap

Future features, outside V1:

- IoT sensor integration.
- Real university maintenance database integration.
- GPS or map-based location detection.
- Image-based issue reporting.
- Voice reporting.
- Energy-meter integration.
- Water-flow sensor integration.
- Automated maintenance system integration.
- Predictive maintenance.
- Campus sustainability dashboard.
- Multi-campus support.
- Role-based facilities team portal.
- Advanced impact estimation for water, energy, and waste savings.

## 24. Phase 1 Definition Of Done

Phase 1 is complete when:

- `use-cases/campusgreen/` exists.
- Product name is finalized.
- Problem statement is finalized.
- Solution scope is finalized.
- SDG alignment is finalized.
- User scenarios are defined.
- Agent responsibilities are defined.
- Agent boundaries are defined.
- Tools are specified.
- Data model is specified.
- Issue lifecycle is specified.
- Memory requirements are specified.
- Error behavior is specified.
- MVP scope is separated from future roadmap.
- `SPEC.md` is completed.
- The team reviews and agrees on the specification before Phase 2 implementation begins.
