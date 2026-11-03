"""Tool registry.

Tools register themselves with `@tool(...)`. The config's `tools.enabled`
list decides which are offered to the model, so a business deployment can
add its own tools (e.g. book_appointment) without touching the agent loop.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, replace
from typing import Callable

from .knowledge import KnowledgeBase
from .notifications import Notifier

log = logging.getLogger(__name__)

# A loose format check; it only needs to catch obvious mistakes.
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# What tool handlers can use. Built fresh for each call so visitors' text is never shared.
@dataclass
class ToolContext:
    kb: KnowledgeBase
    notifier: Notifier
    visitor_text: str | None = None  # What the visitor has written this conversation, when known


# A tool's definition, along with the function that runs it.
@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    handler: Callable[..., dict]

    # The tool definition in the format the model API expects.
    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": self.parameters},
        }


# Every tool that exists. The config chooses which ones the model is offered.
REGISTRY: dict[str, Tool] = {}


# Decorator that adds a function to REGISTRY.
def tool(name: str, description: str, parameters: dict):
    def decorator(fn: Callable[..., dict]):
        REGISTRY[name] = Tool(name, description, parameters, fn)
        return fn

    return decorator


# built-ins

@tool(
    name="record_user_details",
    description=(
        "Record that a user wants to get in touch, only after they have written their own email "
        "address in the conversation. Never guess an address or use one from your context."
    ),
    parameters={
        "type": "object",
        "properties": {
            "email": {"type": "string", "description": "The user's email address"},
            "name": {"type": "string", "description": "The user's name, if provided"},
            "notes": {"type": "string", "description": "Context from the conversation worth recording"},
        },
        "required": ["email"],
        "additionalProperties": False,
    },
)
def record_user_details(ctx: ToolContext, email: str, name: str | None = None, notes: str | None = None) -> dict:
    if not EMAIL_RE.match(email.strip()):
        return {"status": "error", "message": "That does not look like a valid email address. Ask the user to check it."}
    # Stops the model saving an address the visitor never gave, such as the owner's own.
    if ctx.visitor_text is not None and email.strip().lower() not in ctx.visitor_text.lower():
        return {"status": "error", "message": "The user has not written that address. Nothing was recorded. Ask for their email."}
    contact_id = ctx.kb.record_contact(email, name, notes)
    ctx.notifier.send(f"New contact: {name or 'Name not provided'} <{email}>. Notes: {notes or '-'}")
    return {"status": "ok", "contact_id": contact_id}


@tool(
    name="record_unknown_question",
    description=(
        "Do not use this if your context has real facts that relate directly to the question, or for "
        "a question you decline as out of scope. Otherwise, call this first whenever you are about to "
        "say you don't have what an in-scope question asked. General statements or offers to discuss "
        "the topic do not count as facts, and questions about expectations or decisions have no facts "
        "unless your context states them. The owner will review it and add an approved answer."
    ),
    parameters={
        "type": "object",
        "properties": {"question": {"type": "string", "description": "The question that could not be answered"}},
        "required": ["question"],
        "additionalProperties": False,
    },
)
def record_unknown_question(ctx: ToolContext, question: str) -> dict:
    # Save first, so the question is kept even if the notification fails.
    question_id = ctx.kb.record_unknown_question(question)
    ctx.notifier.send(f"Unanswered question #{question_id}: {question}")
    return {"status": "ok", "question_id": question_id}


# runtime

# Runs the model's tool requests safely and always returns a result dict.
class ToolRunner:
    def __init__(self, enabled: list[str], ctx: ToolContext):
        # Fail at startup if the config names a tool that doesn't exist.
        unknown = [n for n in enabled if n not in REGISTRY]
        if unknown:
            raise ValueError(f"Unknown tools in config: {unknown}. Available: {sorted(REGISTRY)}")
        self.tools = {n: REGISTRY[n] for n in enabled}
        self.ctx = ctx

    def schemas(self) -> list[dict]:
        return [t.schema() for t in self.tools.values()]

    def run(self, name: str, raw_arguments: str, visitor_text: str | None = None) -> dict:
        # Errors go back to the model as results, so it can recover instead of crashing the chat.
        tool_ = self.tools.get(name)
        if tool_ is None:
            return {"status": "error", "message": f"Unknown tool: {name}"}
        try:
            args = json.loads(raw_arguments or "{}")
        except json.JSONDecodeError:
            return {"status": "error", "message": "Arguments were not valid JSON"}
        try:
            return tool_.handler(replace(self.ctx, visitor_text=visitor_text), **args)
        except TypeError as exc:
            return {"status": "error", "message": f"Bad arguments: {exc}"}
        except Exception:  # noqa: BLE001 - report to the model rather than crash the chat
            log.exception("Tool %s failed", name)
            return {"status": "error", "message": "The tool failed. Apologise and continue without it."}
