# MARIONETTE

A deliberately vulnerable AI agent with tool-calling access to email, a
calendar, and the web -- built as an AI red team / security research
lab exploring what happens when an autonomous agent's *actions* --
not just its answers -- can be steered by content it wasn't supposed to
trust.

> ⚠️ Intentionally insecure by default. Run in an isolated environment
> only. Do not point this at real accounts or expose it publicly.

## Why this exists

Indirect prompt injection is usually framed as a problem with what a
model *says*: attacker-controlled content gets into the context window
and changes the answer. Agentic tool-calling raises the stakes,
because the same technique can change what a model *does* -- and
increasingly, "what it does" means sending real emails, calling real
APIs, and taking real actions with real consequences. OWASP's
`LLM06:2025 Excessive Agency` and MITRE ATLAS's major October 2025
expansion into agent-specific techniques (context poisoning, tool
invocation exfiltration, agent memory manipulation) both exist because
this is where the field's attention has moved.

MARIONETTE is an assistant with five tools -- `search_inbox`,
`read_email`, `get_calendar_events`, `fetch_webpage`, and `send_email`
-- and a `defense_mode` toggle so you can demonstrate the same three
attacks against both a vulnerable and a mitigated configuration.

## Architecture

```
app.py                  Flask + Socket.IO server, live tool-call streaming
core/
  environment.py         the simulated world: inbox, calendar, web, sent log
  tools.py                tool schema + implementations against the environment
  agent.py                 the tool-calling loop (the actual "agent")
  providers.py               multi-provider adapter: Anthropic, OpenAI, Ollama
  defenses.py               toggle-able mitigations: content screening/tagging
                             + human-in-the-loop confirmation for dangerous tools
attacks/
  attack1_tool_hijack.py               injection via a fetched webpage
  attack2_cross_tool_laundering.py       injection via an email body instead
  attack3_context_poisoning.py            a delayed-trigger, multi-turn injection
  attack4_calendar_invite_injection.py     injection via a planted calendar event
templates/, static/       terminal-style UI: chat, live tool-call log, admin panel
```

## The agent loop, in one paragraph

There's no special "agent framework" here -- `core/agent.py`'s
`Agent.run_turn()` is a plain while-loop: send the conversation plus
tool definitions to the model, execute whatever tools it calls, feed
the results back as the next turn, repeat until it stops calling tools
and returns a final answer (or a step cap is hit). That loop is the
entire mechanism. If untrusted text can influence what a tool returns,
it can influence what the model decides to do next in that same loop
-- there's no built-in separation between "a tool result" and "an
instruction" once both are just text sitting in the same context.

## Multi-provider support

The model dropdown supports Claude (Sonnet, Haiku), OpenAI (GPT-4o,
GPT-4o mini), and Ollama for any locally-run, tool-calling-capable
model -- and the model can be switched **mid-session** without
resetting the conversation. That's not a small detail: Anthropic's and
OpenAI's tool-calling APIs return genuinely different response shapes
(Anthropic mixes text and `tool_use` blocks in one content list;
OpenAI returns a separate `tool_calls` array with JSON-string
arguments), so `core/providers.py` stores conversation history in a
provider-neutral format and has each provider translate the *entire*
history to its native shape fresh on every call, rather than storing
history in whichever provider happened to handle the first turn. This
was verified directly, not assumed -- a mocked test starts a
conversation under Sonnet, switches to GPT-4o mid-session, and
confirms the second provider correctly reads and extends history it
didn't generate.

Ollama is handled by the exact same `OpenAICompatibleProvider` class as
real OpenAI, not a separate implementation -- Ollama exposes an
OpenAI-compatible API surface at `/v1`, so pointing the OpenAI SDK's
own `base_url` at a local Ollama server (`http://localhost:11434/v1`
by default) is sufficient. No API key is required for that path; you
do need Ollama actually running locally with a tool-calling-capable
model already pulled (`ollama pull llama3.1` or similar).

## Setup

```bash
git clone <this repo>
cd marionette
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # add your ANTHROPIC_API_KEY
python app.py
```

Open `http://localhost:5001`. The environment seeds itself automatically
with a small legitimate inbox, calendar, and one real webpage -- no
extra setup needed. The **Plant** tab is where you act as the attacker,
planting emails, overwriting webpages, or adding calendar events with
your own payloads. The **Pending** tab is where dangerous tool calls
wait for human approval when `defense_mode` is on -- nothing executes
there until you click Approve or Deny.

Attacks can also be run headless against any running instance:

```bash
python attacks/attack1_tool_hijack.py --target http://localhost:5001
python attacks/attack2_cross_tool_laundering.py
python attacks/attack3_context_poisoning.py
python attacks/attack4_calendar_invite_injection.py
```

## The four attacks

| # | Attack | What it shows | Toggle to fix |
|---|--------|----------------|----------------|
| 1 | Tool hijack via fetched page | A legitimate reason to fetch a webpage (a real invoice link) becomes a vector once that page is compromised -- hidden instructions in the fetched content try to get the agent to email inbox contents to an attacker address. | `defense_mode` ON screens and structurally tags untrusted tool output, and gates `send_email` behind human confirmation regardless of what the model decides. |
| 2 | Cross-tool laundering | Same outcome, but the injection lives in an *email body* read via `read_email` instead of a fetched page -- proving the vector isn't tied to one "obviously risky" tool. | Same two mitigations apply uniformly across every tool in `UNTRUSTED_CONTENT_TOOLS`, not just the one used in Attack #1. |
| 3 | Persistent context poisoning | An injected instruction doesn't act immediately -- it plants a standing directive that sits in conversation history and fires later, on a completely unrelated, legitimate request in the same session. | Same confirmation gate catches the delayed action too, since it's enforced at the point of execution, not at the point the instruction was first read. |
| 4 | Calendar invite injection | The calendar was originally treated as having "no plausible injection path" -- a wrong assumption. A planted event's description field is just as attacker-influenceable as an email body the moment anyone external can put something on your calendar, and the user's question ("what's on my calendar today?") never has to look suspicious. | Same two mitigations, once `get_calendar_events` was added to `UNTRUSTED_CONTENT_TOOLS` -- the fix wasn't a new mechanism, it was closing a gap in which tools the existing mechanism covered. |

Toggle `defense_mode` and re-run any attack for the before/after
comparison -- that's the actual demo, not just the "VULNERABLE" result
on its own.

## A note on what's confirmed vs. what's just built

Everything in `core/` and `attacks/` has been verified with mocked API
responses to confirm the *mechanism* works correctly end-to-end -- the
tool loop, the defense gating, the confirmation flow. That's a
different, weaker claim than "an attack succeeds," though: whether the
model actually follows any given injected instruction is a separate,
real question that only gets answered by running it against the live
API. `findings/FINDINGS_REPORT.md` is written to that standard --
findings are added only once a real transcript exists, and results are
reported as exactly what they are (including refusals), not
generalized past what a single test actually shows.

## Extending this

- Add a second "dangerous" tool (e.g. `create_calendar_event` or a
  fake `transfer_funds`) to show the confirmation-gating pattern
  generalizes past `send_email`.
- Try Attack #3 with a longer delay -- several unrelated turns between
  the poisoning and the legitimate request that triggers it -- to see
  how far the standing instruction actually persists.
- Swap the confirmation gate for a tool-allowlisting policy instead
  (e.g. `send_email` only usable if the current turn's tool-call chain
  started from a direct user request, not from content read via
  `fetch_webpage`/`read_email`) and compare which mitigation holds up
  better against Attack #2's laundering technique.
