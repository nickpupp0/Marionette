# MARIONETTE -- Setup Guide

Step-by-step instructions to get the app running locally. For
architecture and attack details, see `README.md` once you're up.

## Prerequisites

- Python 3.10 or newer
- pip
- An Anthropic API key (from [console.anthropic.com](https://console.anthropic.com)) -- required for the default model options (Sonnet, Haiku). OpenAI and Ollama are also supported and configured separately -- see step 4.

## 1. Get the project files

Unzip `marionette.zip` wherever you want to work from, then move into
the folder:

```bash
cd marionette
```

## 2. Create a virtual environment

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
```

You should see `(venv)` appear at the start of your terminal prompt
once it's active.

## 3. Install dependencies

```bash
pip install -r requirements.txt
```

## 4. Configure your API key

```bash
cp .env.example .env
```

Open `.env` in an editor and set:

```
ANTHROPIC_API_KEY=your_actual_key_here
```

`.env` is already listed in `.gitignore` -- never commit it.

**Using GPT-4o or GPT-4o mini instead of Claude?** Also set
`OPENAI_API_KEY=your_actual_key_here` in `.env`. Only needed if you
actually select one of those in the model dropdown.

**Using Ollama (local)?** No API key needed at all -- but Ollama itself
needs to be installed, running, and have a tool-calling-capable model
already pulled before you select it in the dropdown:

```bash
ollama pull llama3.1
ollama serve   # if it isn't already running as a background service
```

`.env`'s `OLLAMA_MODEL` and `OLLAMA_BASE_URL` default to `llama3.1` and
`http://localhost:11434/v1` -- only change these if you pulled a
different model or run Ollama on a non-default port.

## 5. Start the server

```bash
python app.py
```

You should see Flask/Socket.IO startup output ending with something like:

```
Running on http://127.0.0.1:5001
```

## 6. Open it in your browser

Go to `http://localhost:5001`.

## 7. Confirm it's working

Type a message in the chat box on the left, for example:

```
What's on my calendar this week?
```

You should see a live event appear in the middle panel (a `tool_call`
for `get_calendar_events`), followed by the agent's answer in the chat
window a moment later. If that happens, everything's wired up correctly.

## What you're looking at

- **Left -- Agent Console**: chat with the agent, exactly like a real end user would.
- **Middle -- Live tool-call log**: every tool call the agent makes, streamed in real time, including its input and a preview of the result.
- **Right -- admin/attacker panel**, four tabs:
  - **Plant**: act as the attacker -- plant a malicious email, or plant/overwrite a webpage.
  - **Attacks**: one-click buttons to run the three scripted attacks in `attacks/`, plus a reset button to wipe back to seed data.
  - **Pending**: dangerous tool calls (currently `send_email`) staged here when `defense_mode` is on. Nothing executes until you click Approve or Deny.
  - **Environment**: browse the current inbox, web store, calendar, and sent-email log.
- **Top-right toggle**: `defense_mode` -- flip between VULNERABLE and DEFENDED to compare behavior on the same attack.

## Troubleshooting

- **Auth / missing API key errors** -- double-check `.env` has a real key (not the placeholder text) and that you're running `python app.py` from inside the `marionette/` folder itself, so `python-dotenv` can find `.env`.
- **Port 5001 already in use** -- set `PORT=5002` (or any free port) in `.env`, or stop whatever else is using 5001.
- **`ModuleNotFoundError`** -- your virtual environment probably isn't active. Confirm you see `(venv)` in your prompt, then re-run `pip install -r requirements.txt`.
- **Agent answers without calling any tool** -- check the middle panel for `tool_call` events. If none appear, try rephrasing your message to something that clearly requires looking something up (e.g. "search my inbox for invoices," "what's on my calendar Thursday") rather than a general question it can answer from its own knowledge.
- **`ConnectionRefusedError` when running an attack script** -- make sure `python app.py` is still running in another terminal; the attack scripts talk to it over real HTTP.

## Next steps

- Read `README.md` for the architecture overview and the three attacks in detail.
- Try planting your own email or webpage via the **Plant** tab and see what the agent does with it before running the scripted attacks.
