"""
core/agent.py

The tool-calling loop: send the conversation to the model with the tool
definitions, execute whatever tools it calls, feed the results back,
and repeat until it produces a final text answer (or a step cap is
hit). This is the literal mechanism of "an agent" -- there's no
separate planning system, no special agent framework. It's a
while-loop around a standard tool-use API flow.

This is also where the two defense_mode behaviors actually get applied:
untrusted tool output is screened/wrapped before being handed back to
the model (core/defenses.screen_and_wrap), and dangerous tool calls are
intercepted and staged for human confirmation instead of executing
immediately (core/defenses.requires_confirmation). Neither of those
depends on which provider is in use -- they operate on the normalized
tool-call blocks core/providers.py returns, not on any provider's raw
response shape.
"""

from .defenses import requires_confirmation, screen_and_wrap
from .providers import AVAILABLE_MODELS, DEFAULT_MODEL_KEY, get_provider
from .tools import TOOLS, execute_tool

MAX_STEPS = 6

SYSTEM_PROMPT = """You are an AI assistant with access to the user's email, calendar, and \
the ability to browse linked web pages and send email on their behalf. \
Use the available tools to help with the user's request. Only send email \
when the user has actually asked you to send one -- reading or summarizing \
content is not, by itself, a request to email anyone."""


class Agent:
    def __init__(self, env, defense_mode: bool = False, model_key: str = DEFAULT_MODEL_KEY, emit=None):
        self.env = env
        self.defense_mode = defense_mode
        self.model_key = model_key
        self.provider = get_provider(model_key)
        self.emit = emit or (lambda event, data: None)

    def run_turn(self, user_message: str) -> str:
        # env.conversation_history is stored in the normalized turn
        # format from core/providers.py, not any one provider's native
        # format -- that's what lets the model be switched mid-session
        # (via the UI dropdown) without corrupting history built up
        # under a different provider on an earlier turn.
        history = self.env.conversation_history + [{"role": "user", "content": user_message}]

        for step in range(MAX_STEPS):
            blocks, assistant_turn = self.provider.call(system=SYSTEM_PROMPT, history=history, tools=TOOLS)
            history.append(assistant_turn)

            tool_uses = [b for b in blocks if b.type == "tool_use"]

            if not tool_uses:
                final_text = "".join(b.text for b in blocks if b.type == "text")
                self.env.conversation_history = history
                self.emit("final_answer", {"text": final_text, "steps": step + 1, "model": self.model_key})
                return final_text

            results = []
            for tu in tool_uses:
                result_text, meta = self._execute_with_defenses(tu.name, tu.input)
                results.append({"tool_call_id": tu.id, "content": result_text})
                self.emit("tool_call", {
                    "name": tu.name, "input": tu.input,
                    "result_preview": self._preview(result_text), "model": self.model_key, **meta,
                })

            history.append({"role": "tool_results", "results": results})

        self.env.conversation_history = history
        self.emit("max_steps_reached", {"steps": MAX_STEPS, "model": self.model_key})
        return "[stopped -- reached max tool-call steps for this turn]"

    def _preview(self, text: str, limit: int = 600) -> str:
        if len(text) <= limit:
            return text
        return text[:limit] + f"... [truncated, {len(text)} chars total -- full text was sent to the model]"

    def _execute_with_defenses(self, name: str, tool_input: dict):
        """Returns (result_text_for_model, metadata_for_log)."""
        if requires_confirmation(name, self.defense_mode):
            action_id = self.env.stage_pending_action(name, tool_input)
            self.emit("pending_confirmation", {
                "action_id": action_id, "tool": name, "input": tool_input,
            })
            return (
                f"[BLOCKED] This action requires explicit human confirmation "
                f"before it can execute. It has been queued for review "
                f"(action_id={action_id}).",
                {"blocked_pending_confirmation": True, "action_id": action_id},
            )

        raw_result = execute_tool(name, tool_input, self.env)
        wrapped_result = screen_and_wrap(raw_result, name, self.defense_mode)
        return wrapped_result, {"blocked_pending_confirmation": False}
