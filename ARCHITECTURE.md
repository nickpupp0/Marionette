# MARIONETTE -- Architecture & File Guide

A file-by-file explanation of how this project actually works, written
for interview prep. The goal is being able to explain the *legitimate*
mechanism -- how tool use / function calling actually works -- not just
which files make the attacks succeed.

---

## The one-paragraph version

This is a Flask app wrapped around a plain while-loop. Each turn, the
full conversation plus five tool definitions get sent to Claude. If the
response asks to call a tool, the app executes it for real and sends
the result back as the next message; if the response is just text, that
text is the final answer and the loop stops. "Agent" doesn't mean
anything more exotic than that loop -- the model decides, one step at a
time, whether it has enough information to answer or needs to call
another tool first.

---

## Request lifecycle

**A chat message:**
1. Browser sends `POST /api/chat` with `{message}`
2. `app.py`'s `chat()` calls `Agent.run_turn()`
3. `agent.py` sends the full conversation (including prior turns -- see why that matters below) plus `TOOLS` to Claude
4. If the response contains `tool_use` blocks: for each one, `_execute_with_defenses()` checks whether it's a dangerous action (`requires_confirmation`). If not, it executes for real and the result is screened/tagged (`screen_and_wrap`) if it came from an untrusted-content tool. If it *is* dangerous, the call is staged as a `pending_action` and a placeholder "[BLOCKED]" result is returned to the model instead of actually running it.
5. Every tool result gets appended to the conversation, and the loop repeats -- Claude sees what happened and decides what to do next, up to `MAX_STEPS` times.
6. Once a response has no `tool_use` blocks, that's the final answer. It's returned to the browser, and the full message history is saved back into `env.conversation_history` for the *next* chat call.

**A plant (attacker) request:**
1. `POST /api/admin/plant_email` or `plant_webpage`
2. Stored directly into `Environment`, with **no screening at this point**.

That last detail is a deliberate design choice, worth being able to
explain on its own terms: screening happens at *read* time
(`screen_and_wrap`, called inside the agent loop) rather than at plant
time, because MARIONETTE's content -- emails, web pages -- gets fetched
fresh by a tool call every time it's relevant to a conversation, not
indexed once into a static store the way a search/retrieval system's
knowledge base is. The general idea (screen untrusted content before it
reaches the model) is the same regardless of architecture; *where* that
checkpoint lives has to match how the content actually flows through
the system. A system that indexes documents once and serves them many
times would want to screen at ingest instead, since that's the point
where a single piece of bad content can affect every future query
rather than just the one turn that happened to fetch it.

**Approving a pending action:**
1. `POST /api/pending/<id>/resolve` with `{approve}`
2. If approved, `execute_tool()` is called **directly** with the exact staged input -- not sent back to the model for a fresh decision. Approving a permission prompt runs precisely the action that was proposed, the same way approving a `sudo` prompt or a Claude Code tool-permission dialog does.

---

## File-by-file

### `app.py`
The Flask + Socket.IO server -- every request's entry point. Holds two
pieces of global state: `STATE["defense_mode"]` and the single shared
`env` (an `Environment` instance). Worth being able to name the
limitation this creates: it's an in-memory Python object, not a real
database, so this works because it's a single-process demo. A real
multi-worker deployment (normal for a production web app) would need
this state in an actual external database instead, so every worker
process sees the same inbox, calendar, and pending actions -- otherwise
different workers would silently disagree with each other.

The `/api/pending/<action_id>/resolve` route is the human-in-the-loop
control point -- worth being able to point to directly as "this is the
line where a human, not the model, decides whether a consequential
action actually happens."

### `core/environment.py`
The simulated world: `inbox`, `calendar`, `web`, `sent_log`,
`pending_actions`, and -- importantly -- `conversation_history`, which
persists across chat turns within a session rather than resetting each
time. That persistence is what makes Attack #3 possible at all: if each
`/api/chat` call started a brand-new conversation, a standing
instruction planted in one turn would have nothing to survive in by the
next.

### `core/tools.py`
Two things: the `TOOLS` list (the actual JSON schema sent to the
Anthropic API's `tools` parameter -- this *is* what "giving a model tool
access" means at the wire level, nothing more mysterious than a list of
name/description/input_schema dicts) and `execute_tool()`, which
performs the real read or write against `Environment` once the model
has asked for it.

**Worth knowing for an interview:** be able to describe the actual
shape of a tool-use exchange -- the model's response contains a
`tool_use` content block with an `id`, a `name`, and an `input` object;
the application executes that and sends back a `tool_result` block
referencing the same `id` with the output as its `content`. That
request/response shape is identical across every major LLM API that
supports function calling, not something specific to this lab.

### `core/agent.py`
The loop itself, capped at `MAX_STEPS` -- a safety/cost control, since
without a cap a model that keeps deciding to call more tools (or a
successfully-poisoned one deliberately looping) could run indefinitely.
`_execute_with_defenses()` is the dispatch point: it checks the tool
name against the two sets defined in `defenses.py`
(`UNTRUSTED_CONTENT_TOOLS` vs. `ACTION_TOOLS`) and applies the matching
mitigation -- content screening for one category, confirmation gating
for the other. Those are different problems with different fixes: no
amount of screening *content* stops a model from *deciding* to call a
dangerous tool; that requires an authorization control instead.

Notice what `agent.py` does *not* contain: no `if provider == "openai"`
branching, no Anthropic-specific response parsing. It calls
`self.provider.call(...)` and works with whatever normalized blocks
come back, regardless of which model is actually running. That
separation is deliberate -- see `core/providers.py` below.

### `core/providers.py`
The adapter layer that makes multi-provider support possible without
`agent.py` needing to know which provider is active. Two things worth
being able to explain clearly:

1. **Anthropic and OpenAI's tool-calling APIs are structurally
   different, not just different endpoints.** Anthropic returns a
   `response.content` list mixing text and `tool_use` blocks in one
   sequence. OpenAI returns `message.content` (text) and a *separate*
   `message.tool_calls` list, where each call's arguments arrive as a
   JSON *string* that has to be parsed, not a dict. Continuing the
   conversation after a tool call differs too: Anthropic expects one
   `user`-role message containing multiple `tool_result` blocks; OpenAI
   expects one separate `{"role": "tool", ...}` message per call. Two
   real, different wire formats -- this module's job is translating
   between them, not just picking which SDK to call.
2. **Conversation history is stored provider-neutral, not in either
   API's native shape.** This matters specifically because MARIONETTE's
   `conversation_history` persists across chat turns (that's the whole
   mechanism behind Attack #3), and the model can be switched mid-session
   via the UI dropdown. If history were stored in, say, Anthropic's
   native format, switching to GPT-4o mid-conversation would hand the
   OpenAI SDK a payload shape it doesn't understand. Instead, every
   provider's `call()` method takes the same small set of normalized
   turn shapes and translates the *entire* history to its native format
   fresh, on every call -- so switching providers mid-session just
   works, verified directly with a test that starts a conversation
   under Sonnet and continues it under GPT-4o.

Ollama isn't a separate class -- it's the same `OpenAICompatibleProvider`
pointed at a different `base_url` with no API key. Ollama's local
server speaks the OpenAI wire format at `/v1`, so there's nothing
Ollama-specific to implement once the OpenAI path exists.

**Worth knowing for an interview:** be able to state plainly why this
needed an adapter layer at all -- "different providers' function-calling
APIs aren't interchangeable at the wire level, even though the concept
(model asks to call a named tool with arguments) is the same everywhere."
That's a real, common piece of production LLM-application engineering,
not something specific to this lab.

### `core/defenses.py`
`INJECTION_PATTERNS` -- a regex-based screen against common injection
phrasing, plus a few agent-specific patterns for persistence-style
language ("from now on," "always cc/bcc"), relevant to Attack #3. Note:
the original "ignore...instructions" pattern here had a real bug -- it
only matched one word between "ignore" and "instructions," so it missed
the single most common phrasing, "ignore all previous instructions." It
was caught through direct testing and fixed. Worth mentioning as a live
example of why pattern-based detection is inherently fragile: even a
rule built specifically to catch this phrase, written carefully, still
had a gap that only testing surfaced.

`requires_confirmation()` is the other half -- deliberately simple,
deliberately not a detection problem. It doesn't try to guess whether a
given `send_email` call is malicious; it just always requires a human
to say yes when `defense_mode` is on, regardless of how legitimate the
request looks. That's the actual industry-recommended posture for
high-consequence agent actions (OWASP LLM06, Excessive Agency): don't
try to classify intent, remove the agent's unsupervised authority to
act.

### `attacks/attack1_tool_hijack.py`, `attack2_cross_tool_laundering.py`, `attack3_context_poisoning.py`, `attack4_calendar_invite_injection.py`
Standalone HTTP clients -- they plant content and send chat messages
exactly the way a browser would, so they're repeatable proof-of-concept
evidence rather than one-time manual observations. `attack3` is the
only one that makes two separate `/api/chat` calls in sequence, since
persistence across turns is the entire point of that scenario.
`attack4` exists because an earlier version of this lab explicitly
excluded the calendar from `UNTRUSTED_CONTENT_TOOLS` with a comment
claiming it had no plausible injection path -- a real user question
("can I plant a calendar event?") is what surfaced that gap. Worth
remembering as a general instinct: any tool where a third party can put
text in front of the agent is a candidate for this list, and "no
plausible injection path" is a claim worth testing, not assuming.

### `templates/index.html`, `static/style.css`, `static/app.js`
A three-panel layout (chat / live log / admin), with two
MARIONETTE-specific additions: the **Pending** tab (renders staged
actions with Approve/Deny buttons wired to
`/api/pending/<id>/resolve`) and the **Environment** tab's four
sub-views (inbox/web/calendar/sent) for inspecting the simulated
world's current state at any point.

---

## Glossary -- terms worth being able to define cold

- **Tool use / function calling:** the mechanism by which an LLM API response can include a structured request to call a specific function with specific arguments, instead of (or alongside) plain text -- letting the surrounding application execute real code and feed the result back into the conversation.
- **Agent loop:** repeatedly calling the model, executing whatever tools it requests, and feeding results back until it stops requesting tools. This is the entire mechanical definition of "an AI agent" in this context -- no separate planning system required.
- **Human-in-the-loop:** a control that pauses an automated system so a person can explicitly approve a consequential action before it executes, rather than trusting the system's own judgment.
- **Context poisoning:** injecting content into a model's ongoing conversation history (not just a single request) so it influences behavior in later, otherwise-unrelated turns.
- **Excessive agency (OWASP LLM06):** the risk category covering agents granted more autonomy, tool access, or unsupervised permission to act than their task actually requires.
- **Indirect vs. direct prompt injection (agentic context):** direct means the user's own message tries to override behavior; indirect means the override is hidden in something a *tool* returns -- a fetched page, an email, a calendar entry -- so the person making the request never has to do anything suspicious themselves.
