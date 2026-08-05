"""
MARIONETTE -- a deliberately vulnerable AI agent with tool-calling access
to email, calendar, and the web, built as an AI red-teaming portfolio /
research lab exploring how indirect prompt injection can steer what an
agent *does* (tool-calling actions), not just what it says.

DO NOT deploy this outside an isolated environment. It is intentionally
insecure by default. Toggle "defense_mode" in the UI (or via
/api/admin/defense_mode) to compare vulnerable vs. mitigated behavior.
"""

import contextlib
import io
import os
import threading

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request
from flask_socketio import SocketIO

from attacks import (
    attack1_tool_hijack,
    attack2_cross_tool_laundering,
    attack3_context_poisoning,
    attack4_calendar_invite_injection,
)
from core.agent import Agent
from core.environment import Environment
from core.tools import execute_tool

ATTACK_MODULES = {
    "attack1": attack1_tool_hijack,
    "attack2": attack2_cross_tool_laundering,
    "attack3": attack3_context_poisoning,
    "attack4": attack4_calendar_invite_injection,
}

load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-only-not-for-prod")
socketio = SocketIO(app, cors_allowed_origins="*")

STATE = {"defense_mode": False, "model_key": "sonnet"}
env = Environment()


def emit_event(event, data):
    socketio.emit(event, data)


def get_agent():
    return Agent(env, defense_mode=STATE["defense_mode"], model_key=STATE["model_key"], emit=emit_event)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.get_json(force=True)
    message = data.get("message", "")
    agent = get_agent()
    answer = agent.run_turn(message)
    return jsonify({"answer": answer})


@app.route("/api/admin/plant_email", methods=["POST"])
def plant_email():
    data = request.get_json(force=True)
    email = env.plant_email(
        sender=data.get("sender", "unknown@example.com"),
        subject=data.get("subject", ""),
        body=data.get("body", ""),
    )
    emit_event("ingest", {"kind": "email", **email})
    return jsonify({"email": email})


@app.route("/api/admin/plant_webpage", methods=["POST"])
def plant_webpage():
    data = request.get_json(force=True)
    page = env.plant_webpage(url=data.get("url", ""), content=data.get("content", ""))
    emit_event("ingest", {"kind": "webpage", **page})
    return jsonify({"webpage": page})


@app.route("/api/admin/plant_calendar_event", methods=["POST"])
def plant_calendar_event():
    data = request.get_json(force=True)
    event = env.plant_calendar_event(
        date=data.get("date", ""),
        title=data.get("title", ""),
        description=data.get("description", ""),
        attendees=data.get("attendees", []),
    )
    emit_event("ingest", {"kind": "calendar_event", **event})
    return jsonify({"event": event})


@app.route("/api/inbox")
def list_inbox():
    return jsonify(list(env.inbox.values()))


@app.route("/api/web")
def list_web():
    return jsonify(list(env.web.values()))


@app.route("/api/calendar")
def list_calendar():
    return jsonify(list(env.calendar.values()))


@app.route("/api/sent")
def list_sent():
    return jsonify(env.sent_log)


@app.route("/api/pending")
def list_pending():
    return jsonify(list(env.pending_actions.values()))


@app.route("/api/pending/<action_id>/resolve", methods=["POST"])
def resolve_pending(action_id):
    """
    Human-in-the-loop confirmation endpoint. Approving does NOT go back
    to the model for another decision -- it executes exactly the staged
    tool call, for real, the same way approving a permission prompt in a
    real agentic tool runs precisely the action that was proposed.
    """
    data = request.get_json(force=True)
    approve = bool(data.get("approve", False))
    action = env.resolve_pending_action(action_id, approve)
    if not action:
        return jsonify({"error": "unknown action_id"}), 404

    result = None
    if approve:
        result = execute_tool(action["tool"], action["input"], env)

    emit_event("pending_resolved", {"action_id": action_id, "approved": approve, "result": result})
    return jsonify({"action": action, "result": result})


@app.route("/api/admin/state")
def get_state():
    """
    Lets the UI sync to the server's actual current state rather than
    just defaulting to whatever the HTML/JS initializes to. This exists
    because of a real bug: with no sync-on-load, the model dropdown and
    defense_mode toggle would silently go stale the moment the server
    restarted for any reason (including Flask's own debug-mode
    auto-reloader, which restarts on every source file save) while a
    browser tab stayed open across it -- the UI would keep showing
    whatever was selected before the restart, while the server had
    already reverted to defaults underneath it.
    """
    return jsonify({"defense_mode": STATE["defense_mode"], "model_key": STATE["model_key"]})


@app.route("/api/admin/defense_mode", methods=["POST"])
def set_defense_mode():
    data = request.get_json(force=True)
    STATE["defense_mode"] = bool(data.get("enabled", False))
    emit_event("defense_mode", {"enabled": STATE["defense_mode"]})
    return jsonify({"defense_mode": STATE["defense_mode"]})


@app.route("/api/admin/model", methods=["POST"])
def set_model():
    from core.agent import AVAILABLE_MODELS
    data = request.get_json(force=True)
    model_key = data.get("model", "sonnet")
    if model_key not in AVAILABLE_MODELS:
        return jsonify({"error": f"unknown model '{model_key}'", "available": list(AVAILABLE_MODELS)}), 400
    STATE["model_key"] = model_key
    emit_event("model_changed", {"model": model_key})
    return jsonify({"model": model_key})


@app.route("/api/attacks/run/<name>", methods=["POST"])
def run_attack(name):
    module = ATTACK_MODULES.get(name)
    if not module:
        return jsonify({"error": f"unknown attack '{name}'"}), 404

    target = request.host_url.rstrip("/")

    def _run():
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                module.run(target)
        except Exception as e:  # noqa: BLE001
            buf.write(f"\n[error] {e}\n")
        with app.app_context():
            emit_event("attack_log", {"name": name, "output": buf.getvalue()})

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"status": "started", "attack": name})


@app.route("/api/admin/reset", methods=["POST"])
def reset():
    env.reset()
    emit_event("reset", {})
    return jsonify({"status": "reset"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    socketio.run(app, debug=True, port=port)
