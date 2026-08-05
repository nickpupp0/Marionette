"""
core/providers.py

Anthropic's and OpenAI's tool-calling APIs are not the same shape.
Anthropic returns a response.content list mixing text and tool_use
blocks; OpenAI returns message.content (text) and a separate
message.tool_calls list where each call's arguments are a JSON string
that needs parsing. Continuing a conversation after a tool call differs
too: Anthropic expects one user message containing multiple
tool_result blocks, OpenAI expects one separate {"role": "tool", ...}
message per call.

This module is the adapter layer that hides that difference from
core/agent.py's loop. The critical design constraint: MARIONETTE's
conversation_history persists across chat turns (that's what makes
Attack #3's context poisoning possible at all), and the model can be
switched mid-session via the UI dropdown -- so history CANNOT be stored
in any one provider's native format, or switching providers would
silently corrupt it. Instead, history is stored as a small set of
normalized turn shapes, and each provider translates the *entire*
history to its native format fresh on every call:

  {"role": "user", "content": "<text>"}
  {"role": "assistant", "content": "<text or None>", "tool_calls": [
      {"id": ..., "name": ..., "input": {...}}, ...
  ]}
  {"role": "tool_results", "results": [
      {"tool_call_id": ..., "content": "<result text>"}, ...
  ]}

Ollama is handled by the exact same code path as OpenAI, not a
separate implementation -- verified directly (not assumed) that
Ollama exposes an OpenAI-compatible /v1 surface, so pointing the
OpenAI SDK's own `base_url` at a local Ollama server is sufficient.
"""

import json
import os

from anthropic import Anthropic
from openai import OpenAI


class NormalizedBlock:
    """Provider-agnostic representation of one piece of a model's response."""

    def __init__(self, type_, **kwargs):
        self.type = type_
        for k, v in kwargs.items():
            setattr(self, k, v)


def _to_openai_tools(anthropic_tools):
    """Translates the Anthropic-shaped TOOLS list (core/tools.py) into
    OpenAI's function-calling schema."""
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        }
        for t in anthropic_tools
    ]


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, model):
        self.model = model
        self.client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    def call(self, *, system, history, tools):
        native_messages = []
        for turn in history:
            if turn["role"] == "user":
                native_messages.append({"role": "user", "content": turn["content"]})
            elif turn["role"] == "assistant":
                content = []
                if turn.get("content"):
                    content.append({"type": "text", "text": turn["content"]})
                for tc in turn.get("tool_calls", []):
                    content.append({"type": "tool_use", "id": tc["id"], "name": tc["name"], "input": tc["input"]})
                native_messages.append({"role": "assistant", "content": content})
            elif turn["role"] == "tool_results":
                native_messages.append({"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": r["tool_call_id"], "content": r["content"]}
                    for r in turn["results"]
                ]})

        response = self.client.messages.create(
            model=self.model, max_tokens=1000, system=system,
            messages=native_messages, tools=tools,
        )

        blocks, tool_calls, text_parts = [], [], []
        for b in response.content:
            if b.type == "text":
                blocks.append(NormalizedBlock("text", text=b.text))
                text_parts.append(b.text)
            elif b.type == "tool_use":
                blocks.append(NormalizedBlock("tool_use", id=b.id, name=b.name, input=b.input))
                tool_calls.append({"id": b.id, "name": b.name, "input": b.input})

        assistant_turn = {"role": "assistant", "content": "".join(text_parts) or None, "tool_calls": tool_calls}
        return blocks, assistant_turn


class OpenAICompatibleProvider:
    """Handles both real OpenAI and Ollama -- Ollama's /v1 endpoint speaks
    the same wire format, so this is one implementation, not two."""

    name = "openai"

    def __init__(self, model, base_url=None, api_key=None):
        self.model = model
        self.client = OpenAI(
            api_key=api_key or os.environ.get("OPENAI_API_KEY", "not-needed-for-ollama"),
            base_url=base_url,
        )

    def call(self, *, system, history, tools):
        native_messages = [{"role": "system", "content": system}]
        for turn in history:
            if turn["role"] == "user":
                native_messages.append({"role": "user", "content": turn["content"]})
            elif turn["role"] == "assistant":
                msg = {"role": "assistant", "content": turn.get("content")}
                if turn.get("tool_calls"):
                    msg["tool_calls"] = [
                        {"id": tc["id"], "type": "function",
                         "function": {"name": tc["name"], "arguments": json.dumps(tc["input"])}}
                        for tc in turn["tool_calls"]
                    ]
                native_messages.append(msg)
            elif turn["role"] == "tool_results":
                for r in turn["results"]:
                    native_messages.append({
                        "role": "tool", "tool_call_id": r["tool_call_id"], "content": r["content"],
                    })

        response = self.client.chat.completions.create(
            model=self.model, messages=native_messages, tools=_to_openai_tools(tools),
        )
        msg = response.choices[0].message

        blocks, tool_calls = [], []
        if msg.content:
            blocks.append(NormalizedBlock("text", text=msg.content))
        for tc in (msg.tool_calls or []):
            input_dict = json.loads(tc.function.arguments)
            blocks.append(NormalizedBlock("tool_use", id=tc.id, name=tc.function.name, input=input_dict))
            tool_calls.append({"id": tc.id, "name": tc.function.name, "input": input_dict})

        assistant_turn = {"role": "assistant", "content": msg.content, "tool_calls": tool_calls}
        return blocks, assistant_turn


AVAILABLE_MODELS = {
    "sonnet": {"provider": "anthropic", "model": "claude-sonnet-4-6", "label": "Claude Sonnet"},
    "haiku": {"provider": "anthropic", "model": "claude-haiku-4-5-20251001", "label": "Claude Haiku"},
    "gpt-4o": {"provider": "openai", "model": "gpt-4o", "label": "GPT-4o"},
    "gpt-4o-mini": {"provider": "openai", "model": "gpt-4o-mini", "label": "GPT-4o mini"},
    "ollama": {"provider": "ollama", "model": None, "label": "Ollama (local)"},
}
DEFAULT_MODEL_KEY = "sonnet"


def get_provider(model_key: str):
    """Factory: builds the right Provider instance for a model_key,
    including pointing Ollama at a local server with no API key."""
    entry = AVAILABLE_MODELS.get(model_key, AVAILABLE_MODELS[DEFAULT_MODEL_KEY])

    if entry["provider"] == "anthropic":
        return AnthropicProvider(model=entry["model"])

    if entry["provider"] == "openai":
        return OpenAICompatibleProvider(model=entry["model"])

    if entry["provider"] == "ollama":
        model = os.environ.get("OLLAMA_MODEL", "llama3.1")
        base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1")
        return OpenAICompatibleProvider(model=model, base_url=base_url, api_key="ollama")

    raise ValueError(f"Unknown provider for model_key '{model_key}'")
