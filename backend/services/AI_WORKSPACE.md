# AI Workspace — Backend Architecture (Sprint 6)

The AI Workspace unifies every user-facing AI capability behind one coherent
intelligence layer. It is built from three composable services plus the
`/api/ai` router in `server.py`.

## Layers

```
        ┌─────────────────────────────────────────────┐
route → │  /api/ai/*  (server.py)                      │
        └──────────────┬──────────────────────────────┘
                       │
          ┌────────────▼───────────┐
          │  ModelRouter           │  services/model_router.py
          │  - picks Claude/Gemini │  (Master Orchestrator's model-selection)
          │  - status() table      │
          └───┬────────────────┬───┘
              │                │
   ┌──────────▼─────┐  ┌───────▼───────────┐
   │ PromptLibrary  │  │ AIDebateEngine    │  (existing) — provider fan-out,
   │ prompt_library │  │ ai_debate_engine  │  graceful offline fallback
   └────────────────┘  └───────────────────┘

   AI Memory (services/ai_memory.py) is injected into chat + written by /reflect.
```

## Services

- **`prompt_library.py`** — single source of truth for every system prompt
  (mirrors `.claude/PROMPT.md`). Prompts are versioned, carry a routing
  preference, and are wrapped in the shared Master System Prompt safety
  envelope. `.claude/PROMPT.md` forbids hardcoding prompts in routes — use
  `get_prompt(key, **ctx)`.

- **`model_router.py`** — the only place the app asks "which model should run
  this task?". `run(prompt_key, message, …)` renders the library prompt, routes
  via the debate engine, and reports which provider answered. `status()` powers
  `GET /api/ai/status` and the frontend model-status pill.

- **`ai_memory.py`** — durable per-user memory (`ai_user_memory` collection) +
  conversation sessions derived from `chat_messages`. `build_memory_context()`
  renders the "what I remember about you" block injected into the chat prompt.

## Endpoints (`/api/ai`)

| Method | Path                         | Purpose                                |
|--------|------------------------------|----------------------------------------|
| GET    | `/status`                    | Model Router + provider health         |
| GET    | `/prompts`                   | Prompt Library metadata (no templates) |
| GET    | `/activity`                  | AI Activity Timeline                    |
| GET    | `/memory`                    | Read user AI memory                     |
| PUT    | `/memory`                    | Update user AI memory                   |
| GET    | `/conversations`             | List chat sessions                      |
| POST   | `/conversations`             | Mint a new session id                   |
| DELETE | `/conversations/{id}`        | Delete a session's messages             |
| POST   | `/learn`                     | Learning Mentor                         |
| POST   | `/trade-review`              | Trading Coach (cached on the trade)     |
| POST   | `/portfolio-review`          | Portfolio AI (grounded in live holdings)|
| POST   | `/reflect`                   | Reflection Engine → stores lessons      |

## Rules honoured

- No fabricated data — prompts feed the AI only real numbers; missing data is
  stated, never invented.
- Every response is routed + attributed (`model_used`) for transparency.
- Offline-safe: with no API keys the debate engine's simulated provider returns
  an honest "AI offline" message instead of crashing.

Tests: `backend/tests/test_ai_workspace.py` (hermetic, AI calls stubbed).
