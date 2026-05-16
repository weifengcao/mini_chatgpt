# Mini ChatGPT Implementation Plan

## 1. Purpose

This plan breaks the approved system design into reviewable implementation phases. The goal is to build a working product incrementally, starting with the smallest end-to-end chat path and then adding multi-tenant, agent, tool, and operational capabilities.

## 2. Delivery Principles

- Build vertical slices that can be run and reviewed.
- Keep the agent runtime separate from HTTP handlers.
- Keep tenant isolation explicit in service and database access.
- Add tests alongside behavior that affects data, permissions, streaming, or agent execution.
- Prefer simple infrastructure first, but avoid choices that block the target design.

## 3. Proposed Milestones

### Milestone 0: Project Scaffold

Goal: Establish the application structure, local development workflow, and baseline quality checks.

Scope:

- Create backend FastAPI project.
- Create frontend React and TypeScript project.
- Add Docker Compose for PostgreSQL and Redis.
- Add `.env.example` files.
- Add linting and formatting commands.
- Add README setup instructions.

Acceptance criteria:

- Backend starts locally.
- Frontend starts locally.
- PostgreSQL and Redis start through Docker Compose.
- A health endpoint returns success.
- Basic automated test command runs.

Suggested files:

```text
backend/
  app/
    main.py
    config.py
    api/
    services/
    db/
    agent/
    gateways/
      model_gateway.py
      tool_gateway.py
    providers/
  tests/

frontend/
  src/
  index.html
  package.json

docker-compose.yml
.env.example
```

### Milestone 1: Authentication And Tenant Foundation

Goal: Add company/user foundations and basic authentication.

Scope:

- Add company model.
- Add user model.
- Add password hashing.
- Add login endpoint.
- Add current-user endpoint.
- Add role-based auth helpers.
- Add tenant-scoped database access pattern.

Acceptance criteria:

- A company super account can be created through a seed command or setup endpoint.
- A user can log in.
- Authenticated requests include user and company context.
- Admin-only endpoints reject employee users.
- Tests cover login and role checks.

Key endpoints:

```text
POST /api/auth/login
POST /api/auth/logout
GET  /api/auth/me
GET  /api/companies/current
```

### Milestone 2: Agent Management

Goal: Let company admins create agents and assign access by user.

Scope:

- Add agent database model.
- Add agent access assignment model.
- Add agent CRUD API.
- Enforce max 20 active agents per company.
- Store model provider, model name, system prompt, and tool allowlist.
- Add admin APIs for assigning agent access by user.
- Add frontend admin screen for agent list and create/edit form.

Acceptance criteria:

- Admin can create, edit, disable, and list agents.
- Admin can assign an agent to a user.
- Employee can list enabled agents they have access to.
- Employee cannot create or modify agents.
- Creating the 21st active agent for a company fails.
- Tests cover tenant isolation, agent limit, and access assignment checks.

Key endpoints:

```text
GET    /api/agents
POST   /api/agents
GET    /api/agents/{agent_id}
PATCH  /api/agents/{agent_id}
DELETE /api/agents/{agent_id}
GET    /api/agents/{agent_id}/access
PUT    /api/agents/{agent_id}/access
```

### Milestone 3: Chat Sessions And Message Persistence

Goal: Add chat sessions, participants, and message history without model integration.

Scope:

- Add chat session model.
- Add participant model.
- Add message model.
- Add server-assigned per-session message sequence numbers.
- Add message idempotency fields and unique constraints.
- Add session CRUD API.
- Add message list API.
- Add frontend chat session list and message timeline.
- Add Redis-backed session presence heartbeat.
- Enforce configurable active-user cap per session, targeting 100 users after validation.
- Enforce agent access before a user can use a session agent.

Acceptance criteria:

- User can create a chat session for an enabled agent.
- User can open an existing session.
- User without agent access is blocked with a clear permission error.
- Messages are persisted and loaded in chronological order.
- Duplicate message-send retry returns the existing user message instead of creating duplicates.
- User receives an error when joining a full session.
- Redis outage fails new joins closed and marks presence as degraded.
- Frontend sends session heartbeat while a session is open.
- Tests cover session creation, tenant isolation, access checks, idempotency, sequence ordering, and capacity enforcement.

Key endpoints:

```text
GET  /api/chat-sessions
POST /api/chat-sessions
GET  /api/chat-sessions/{session_id}
GET  /api/chat-sessions/{session_id}/messages
POST /api/chat-sessions/{session_id}/heartbeat
```

### Milestone 4: Non-Streaming AI Response And Agent Runs

Goal: Build the first complete AI chat path with a model gateway and inspectable agent run records.

Scope:

- Add model gateway interface.
- Complete NVIDIA Llama 3 compatibility spike before provider implementation: endpoint, credentials, streaming behavior, rate limits, and tool-call format.
- Add NVIDIA-served Llama 3 provider implementation behind the model gateway.
- Add agent runtime service.
- Add per-session run guard that allows only one active agent run per session.
- Add `agent_runs` database model.
- Add `agent_step_runs` database model.
- Add `model_requests` database model.
- Add `agent_run_evidence` database model.
- Add configurable agentic loop limits with platform defaults and optional agent overrides.
- Add context assembly from agent prompt and recent messages.
- Persist user and assistant messages.
- Persist one agent run and ordered agent step runs per user message.
- Persist running/complete/failed/partial run states and ensure one active agent run per session.
- Persist effective loop limits and observed counters on each agent run.
- Persist evidence records linking the user message, context, model output, and final response.
- Add frontend message composer.
- Add backend model timeout, loop-limit, and error handling.

Acceptance criteria:

- User can send a message in a session.
- Backend calls the configured model through the model gateway.
- A session has at most one running agent run.
- Additional sends while the agent is responding return `SESSION_BUSY`.
- Assistant response is persisted.
- Agent run, step runs, model request, and evidence records are persisted.
- Agent run stops with `AGENT_LOOP_LIMIT_EXCEEDED` when configured loop limits are exceeded.
- Diagnostics data shows effective loop limits and final counter values.
- Frontend displays user and assistant messages.
- Provider API key is read from backend environment only.
- Tests use a fake model gateway and do not call the real model provider.
- Tests cover max agent steps, max model calls, max run duration, busy-session behavior, and one-running-run enforcement.

Key endpoint:

```text
POST /api/chat-sessions/{session_id}/messages
```

### Milestone 5: Streaming Chat

Goal: Stream assistant responses to the browser.

Scope:

- Replace or extend message send endpoint with SSE streaming.
- Add `AgentEvent` and `ModelEvent` types.
- Stream token events from the model gateway.
- Broadcast session stream events to active participants.
- Persist final assistant message after stream completion.
- Mark assistant message failed if stream errors.
- Emit agent step progress events where useful.
- Emit a structured SSE error when an agentic loop limit is exceeded.
- Add frontend SSE handling.
- Add cancel/disconnect handling.
- Prevent model retries after the first token, tool request, or assistant delta has streamed.

Acceptance criteria:

- Assistant response appears incrementally in the UI.
- Other active session participants can observe the streamed assistant response.
- Final assistant message is persisted exactly once.
- Client disconnect does not leave a permanently streaming message.
- Stream disconnect after tokens have started marks the run partial or failed instead of retrying inside the same run.
- Errors are shown in the UI with retry affordance.
- Loop-limit failures show a clear failed or partial response state.
- Tests cover streaming event translation using a fake provider.

Key endpoint:

```text
POST /api/chat-sessions/{session_id}/messages/stream
```

SSE events:

```text
message_started
token
agent_step_completed
message_completed
error
```

### Milestone 6: Tool Execution

Goal: Demonstrate agent task execution through the tool gateway and safe server-side tool registry.

Scope:

- Add tool gateway interface.
- Add tool registry.
- Add tool run database model.
- Add tool allowlist enforcement by agent.
- Add schema validation for tool arguments.
- Add timeout and error handling for tool execution.
- Do not retry tool calls unless the tool is explicitly marked idempotent.
- Add calculator demo tool.
- Link tool calls and tool results to agent step runs.
- Add tool evidence records.
- Enforce max total tool calls and max same-tool calls per agent run.
- Add frontend rendering for tool progress.

Recommended first tool:

- Calculator tool for deterministic task execution.

Acceptance criteria:

- Agent can request an allowed tool.
- Backend validates and executes the tool through the tool gateway.
- Tool run is persisted with arguments, result, status, and timing.
- Tool step runs and evidence records are persisted.
- Tool loop limits stop repeated or excessive tool calls.
- Disallowed tool call is rejected and audited.
- Frontend shows tool started/completed state.
- Tests cover allowed, disallowed, invalid, timeout, retry-disabled, and tool loop-limit paths.

SSE events added:

```text
tool_started
tool_completed
tool_failed
```

### Milestone 7: Admin And Employee Product Polish

Goal: Make the product usable for review beyond backend behavior.

Scope:

- Improve navigation between admin and chat views.
- Add session title editing.
- Add empty, loading, and error states.
- Add basic user invitation or seed-based user creation.
- Add user management screen for admins.
- Add basic company metrics summary.
- Add basic agent metrics summary.
- Add basic chat session metrics summary.
- Add agent run list and detail view.
- Add evidence list rendering in agent run detail.
- Add rate-limit and quota error messaging.

Acceptance criteria:

- Admin can manage agents and users from the UI.
- Admin can assign agent access by user.
- Admin can view basic agent, chat session, model, tool, and agent run metrics.
- Admin can inspect an agent run and its evidence list.
- Employee can choose an accessible agent and start chatting.
- Common error states are understandable.
- UI handles mobile and desktop widths.

### Milestone 8: Observability, Hardening, And Deployment Prep

Goal: Prepare the app for a realistic hosted environment.

Scope:

- Add structured logging.
- Add request IDs.
- Add metrics for latency, errors, model usage, and tool runs.
- Add metrics for agent runs and agent step runs.
- Add health and readiness checks.
- Add rate limits by user and company.
- Add bounded retry/backoff helpers for database transactions and pre-stream model gateway failures.
- Add stale run/message recovery job.
- Add degraded-mode behavior for Redis, telemetry, diagnostics/evidence, model gateway, and tool gateway failures.
- Add production Dockerfiles.
- Add deployment documentation.
- Add diagnostic data retention and redaction rules.

Acceptance criteria:

- Health checks distinguish app, database, and Redis status.
- Logs include request ID, company ID, user ID, session ID, and error code where relevant.
- First-token latency and completion latency are measured.
- Agent run success/failure rate and step duration are measured.
- Rate limits return clear API errors.
- Retryable pre-stream model and database failures use bounded retries with jitter.
- Tool calls are not retried unless explicitly marked idempotent.
- Stale running agent runs and streaming messages are marked failed or partial by recovery.
- Redis outage degrades presence display and fails new joins closed.
- User-facing errors map to stable backend error codes.
- Diagnostic records redact secrets and sensitive tool output.
- Deployment docs describe required environment variables.

## 4. Testing Strategy

### Backend Tests

- Unit tests for domain services.
- API tests for auth, agents, sessions, messages, and streaming.
- Fake model gateway for deterministic agent tests.
- Fake tools for tool runtime tests.
- Tenant isolation tests for all tenant-scoped resources.
- Agent run, step run, diagnostics, and evidence-list tests.
- Loop-limit tests for max steps, model calls, tool calls, same-tool calls, and run duration.
- Failure and resilience tests for model timeout, tool timeout, Redis outage, client disconnect, stale run recovery, busy-session behavior, and idempotent message send.

### Frontend Tests

- Component tests for message rendering.
- Component tests for agent/session list states.
- Mocked API tests for sending messages.
- Mocked SSE tests for streaming tokens and tool events.
- Diagnostics view tests for step timeline and evidence rendering.

### Manual Review Scenarios

- Admin creates company agent.
- Employee creates chat session with an agent.
- Two users join the same session.
- User sends a message and receives streamed response.
- Session capacity limit is enforced.
- Agent executes demo tool.
- Admin views company, agent, session, model, and tool metrics.
- Admin inspects an agent run and its evidence list.
- Model provider failure is shown cleanly.
- Duplicate message-send retry returns the existing message/run instead of creating duplicates.
- Stale running agent run is recovered to failed or partial state.

## 5. Environment Variables

```text
APP_ENV=local
APP_SECRET_KEY=change-me
DATABASE_URL=postgresql+psycopg://mini_chatgpt:mini_chatgpt@localhost:5432/mini_chatgpt
REDIS_URL=redis://localhost:6379/0
AI_PROVIDER=nvidia
NVIDIA_API_KEY=
AI_MODEL=llama3
AGENT_MAX_STEPS=8
AGENT_MAX_MODEL_CALLS=4
AGENT_MAX_TOOL_CALLS=3
AGENT_MAX_SAME_TOOL_CALLS=2
AGENT_MAX_RUN_DURATION_SECONDS=60
MODEL_GATEWAY_MAX_RETRIES=2
DATABASE_TRANSACTION_MAX_RETRIES=2
TOOL_GATEWAY_MAX_RETRIES=0
ACCESS_TOKEN_TTL_MINUTES=30
REFRESH_TOKEN_TTL_DAYS=14
CORS_ORIGINS=http://localhost:5173
```

## 6. Implementation Order Recommendation

The recommended path is:

```text
Milestone 0
Milestone 1
Milestone 2
Milestone 3
Milestone 4
Milestone 5
Milestone 6
Milestone 7
Milestone 8
```

This order keeps the first model integration small while still putting tenant and agent boundaries in place before chat behavior becomes complex.

## 7. Risks And Mitigations

### Risk: Latency Target Depends On External Provider

Mitigation:

- Track first-token latency separately from full response latency.
- Stream as soon as possible.
- Keep prompt context bounded.
- Allow model selection per agent.

### Risk: Agentic Loop Runs Too Long Or Repeats Tool Calls

Mitigation:

- Enforce platform default loop limits.
- Persist effective limits and final counters for diagnostics.
- Stop repeated same-tool calls with `max_same_tool_calls_per_run`.
- Emit `AGENT_LOOP_LIMIT_EXCEEDED` and mark the run failed or partial.

### Risk: Partial Failure Creates Duplicate Or Inconsistent Runs

Mitigation:

- Require idempotency keys on message send requests.
- Persist user message, assistant placeholder, and agent run before external model/tool calls.
- Avoid starting model or tool side effects when the initial database transaction fails.
- Reconcile stale running runs and streaming messages with a recovery job.
- Store stable error codes for every failed step.

### Risk: Tool Execution Can Introduce Unsafe Side Effects

Mitigation:

- Start with read-only or deterministic tools.
- Require tool allowlists.
- Validate arguments with schemas.
- Persist all tool runs.
- Add confirmation flows before external side-effect tools.

### Risk: Agent Run Diagnostics And Evidence Records Can Store Sensitive Data

Mitigation:

- Persist summaries and hashes where full content is not required.
- Redact secrets before writing diagnostic snapshots.
- Mark sensitive tool outputs and hide them from employee-facing diagnostic views.
- Add retention controls for detailed run records.

### Risk: Tenant Isolation Bugs

Mitigation:

- Include `company_id` on tenant-owned tables.
- Centralize tenant-scoped query helpers.
- Add tests that attempt cross-tenant access.

### Risk: Shared Session Concurrency Adds Complexity

Mitigation:

- Use Redis TTL heartbeats for active presence.
- Persist participants separately for history.
- Keep stream generation scoped to one user message at a time and return `SESSION_BUSY` for concurrent sends.

## 8. Review Checklist

- Confirm the proposed stack.
- Confirm whether PostgreSQL should be used from the start.
- Confirm the initial per-session participant cap.
- Confirm whether metrics summaries can be built after streaming/tool execution or must ship earlier.
- Confirm diagnostic retention period and redaction policy.
- Confirm the exact NVIDIA Llama 3 endpoint and credential requirements before implementation.
