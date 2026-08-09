"""
hook_bus.py — The Lifecycle Hook Bus

Generic plumbing for splicing deterministic Python functions into five
points of an agent's tool-execution loop:

  PRE_TOOL_USE   -> fires BEFORE a tool runs. Can BLOCK it (the firewall).
  POST_TOOL_USE  -> fires AFTER a tool runs. Can inject feedback the
                     model will see in its next turn (the engine).
  NOTIFICATION   -> fires for any loggable/interesting event.
  STOP           -> fires when an agent run finishes for good.
  SUBAGENT_STOP  -> fires when a nested/child agent run finishes.

Hooks are plain functions: HookContext in, HookDecision (or None) out.
Nothing here knows about Gemini specifically — this bus would work the
same way wired to any model/tool-calling loop.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable


class HookEvent(str, Enum):
    PRE_TOOL_USE = "pre_tool_use"
    POST_TOOL_USE = "post_tool_use"
    NOTIFICATION = "notification"
    STOP = "stop"
    SUBAGENT_STOP = "subagent_stop"


@dataclass
class HookContext:
    event: HookEvent
    tool_name: str | None = None
    tool_args: dict | None = None
    tool_result: str | None = None
    message: str | None = None
    metadata: dict = field(default_factory=dict)


@dataclass
class HookDecision:
    """What a hook returns.
    block=True    -> stop the tool from executing (PRE_TOOL_USE only).
    feedback=str  -> gets spliced into what the model sees next
                      (POST_TOOL_USE only) — this is the self-repair loop.
    """
    block: bool = False
    reason: str | None = None
    feedback: str | None = None


HookFn = Callable[[HookContext], "HookDecision | None"]


class HookBus:
    def __init__(self):
        self._hooks: dict[HookEvent, list[HookFn]] = {event: [] for event in HookEvent}

    def register(self, event: HookEvent, fn: HookFn) -> None:
        self._hooks[event].append(fn)

    def fire(self, ctx: HookContext) -> list[HookDecision]:
        decisions = []
        for fn in self._hooks[ctx.event]:
            decision = fn(ctx)
            if decision:
                decisions.append(decision)
        return decisions