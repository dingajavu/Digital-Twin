"""The agent loop: knowledge-base lookup, bounded tool calling, tracing."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field

from .config import TwinConfig
from .context import build_system_prompt, format_approved_answers
from .knowledge import KnowledgeBase, QAMatch, open_knowledge_base
from .llm import LLMClient, build_client
from .notifications import Notifier, build_notifier
from .tools import ToolContext, ToolRunner

log = logging.getLogger(__name__)

# Shown to the visitor when anything fails, so they never see an error.
FALLBACK_REPLY = (
    "Sorry, I'm having trouble responding right now. Please try again in a moment."
)


# One tool call the model made, with the result it got back.
@dataclass
class ToolCallRecord:
    name: str
    arguments: str
    result: dict


@dataclass
class TurnTrace:
    """Everything that happened while answering one message.

    Used for logging in production and for assertions in the eval harness."""

    response: str
    kb_matches: list[QAMatch] = field(default_factory=list)
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    tool_rounds: int = 0
    hit_round_cap: bool = False
    latency_ms: int = 0

    @property
    def tools_used(self) -> list[str]:
        return [c.name for c in self.tool_calls]


def _text_content(content) -> str:
    """Gradio may send content as a string or a list of typed parts."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(p.get("text", "") for p in content if isinstance(p, dict))
    return "" if content is None else str(content)


def sanitise_history(history: list[dict], limit: int) -> list[dict]:
    # Keep only user and assistant text. This drops extra Gradio fields and any injected system messages.
    clean = [
        {"role": m["role"], "content": _text_content(m.get("content"))}
        for m in history or []
        if isinstance(m, dict) and m.get("role") in {"user", "assistant"}
    ]
    # Only the most recent messages are sent, to control cost.
    return clean[-limit:] if limit else clean


# Convert the model's tool request to the format the API expects back in the message list.
def _assistant_message(msg) -> dict:
    return {
        "role": "assistant",
        "content": msg.content,
        "tool_calls": [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.function.name, "arguments": tc.function.arguments},
            }
            for tc in msg.tool_calls
        ],
    }


# The agent: takes a visitor's message and returns a reply, using tools as needed.
class Twin:
    def __init__(self, cfg: TwinConfig, llm: LLMClient, kb: KnowledgeBase, tools: ToolRunner, system_prompt: str):
        self.cfg = cfg
        self.llm = llm
        self.kb = kb
        self.tools = tools
        self.system_prompt = system_prompt

    @classmethod
    def from_config(
        cls,
        cfg: TwinConfig,
        *,
        llm: LLMClient | None = None,
        kb: KnowledgeBase | None = None,
        notifier: Notifier | None = None,
        source_texts: dict[str, str] | None = None,
    ) -> "Twin":
        # No knowledge base injected (tests and evals inject one), so open the real one and load seed answers.
        if kb is None:
            kb = open_knowledge_base(cfg)
            if cfg.knowledge_base.seed_file:
                seed = cfg.resolve(cfg.knowledge_base.seed_file)
                if seed.exists():
                    added = kb.import_yaml(seed)
                    if added:
                        log.info("Loaded %d approved answers from %s", added, seed)
        # Anything not injected is built from the config.
        notifier = notifier or build_notifier(cfg.notifications)
        tools = ToolRunner(cfg.tools.enabled, ToolContext(kb=kb, notifier=notifier))
        return cls(
            cfg=cfg,
            llm=llm or build_client(cfg.llm),
            kb=kb,
            tools=tools,
            system_prompt=build_system_prompt(cfg, source_texts),
        )

    
    # Find approved answers similar enough to the visitor's message.
    def _lookup(self, message: str) -> list[QAMatch]:
        kb_cfg = self.cfg.knowledge_base
        if not kb_cfg.enabled:
            return []
        # A database outage shouldn't stop the twin answering from its documents.
        try:
            return self.kb.search(message, threshold=kb_cfg.match_threshold, limit=kb_cfg.max_matches)
        except Exception:  # noqa: BLE001
            log.exception("Knowledge base search failed")
            return []

    # Assemble the full request: system prompt, approved answers, history, then the new message.
    def build_messages(self, message: str, history: list[dict], matches: list[QAMatch]) -> list[dict]:
        messages = [{"role": "system", "content": self.system_prompt}]
        if matches:
            messages.append({"role": "system", "content": format_approved_answers(matches)})
        messages += sanitise_history(history, self.cfg.agent.max_history_messages)
        messages.append({"role": "user", "content": message})
        return messages

    # Answer one message and return a trace of everything that happened.
    def respond(self, message: str, history: list[dict] | None = None) -> TurnTrace:
        start = time.perf_counter()
        matches = self._lookup(message)
        messages = self.build_messages(message, history or [], matches)
        schemas = self.tools.schemas() or None
        trace = TurnTrace(response="", kb_matches=matches)
        # Everything the visitor wrote, so tools can check an email really came from them.
        visitor_text = "\n".join(m["content"] for m in messages if m["role"] == "user")

        reply = None
        # Keep calling the model until it replies with text, up to the tool round limit.
        for _ in range(self.cfg.agent.max_tool_rounds):
            reply = self.llm.complete(messages, schemas)
            if not reply.tool_calls:
                break
            trace.tool_rounds += 1
            # The API requires the tool request to appear before its results.
            messages.append(_assistant_message(reply))
            for tc in reply.tool_calls:
                result = self.tools.run(tc.function.name, tc.function.arguments, visitor_text)
                log.info("tool=%s status=%s", tc.function.name, result.get("status"))
                trace.tool_calls.append(ToolCallRecord(tc.function.name, tc.function.arguments, result))
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result)})
        else:
            # Every round asked for more tools: stop looping and force a text answer.
            trace.hit_round_cap = True
            log.warning("Tool round cap (%d) reached", self.cfg.agent.max_tool_rounds)
            reply = self.llm.complete(messages, None)

        # The model can return no text, so fall back to an empty string.
        trace.response = reply.content or ""
        trace.latency_ms = int((time.perf_counter() - start) * 1000)
        self._record(message, trace)
        return trace

    # Update usage counts for approved answers and log the exchange for review.
    def _record(self, message: str, trace: TurnTrace) -> None:
        ids = [m.id for m in trace.kb_matches]
        # The reply is already written, so a database outage shouldn't lose it.
        try:
            self.kb.mark_used(ids)
            if self.cfg.knowledge_base.log_interactions:
                self.kb.log_interaction(
                    user_message=message,
                    response=trace.response,
                    kb_match_ids=ids,
                    tools_used=trace.tools_used,
                    tool_rounds=trace.tool_rounds,
                    hit_round_cap=trace.hit_round_cap,
                    latency_ms=trace.latency_ms,
                )
        except Exception:  # noqa: BLE001
            log.exception("Failed to record the exchange")

    def chat(self, message: str, history: list[dict]) -> str:
        """Gradio entry point: never raises, always returns text."""
        try:
            return self.respond(message, history).response
        except Exception:  # noqa: BLE001
            log.exception("Failed to answer message")
            return FALLBACK_REPLY
