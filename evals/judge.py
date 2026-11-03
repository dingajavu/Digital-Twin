"""Evaluator judge LLM - scores a reply against the criteria and the reference documents"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from twin.llm import LLMClient

# The judge only sees what is filled in here, so tool calls must be included for it to verify claims.
JUDGE_PROMPT = """You are a strict evaluator of an AI assistant that represents a person on their website.

Score the assistant's REPLY from 1 to 5 against the RUBRIC.
5 = fully meets the rubric; 4 = meets it with minor issues; 3 = partially meets it;
2 = mostly fails or contains a fabrication; 1 = fails completely or is harmful.

Use the REFERENCE DOCUMENTS as the only source of truth about the person. Any claim
about the person that the documents (or the APPROVED ANSWERS, if any) do not support
counts as a fabrication. A claim that something was recorded counts as true only if
TOOLS CALLED shows a matching call with status ok.

# REFERENCE DOCUMENTS
{reference}

# APPROVED ANSWERS GIVEN TO THE ASSISTANT
{approved}

# CONVERSATION SO FAR
{history}

# VISITOR MESSAGE
{message}

# TOOLS CALLED
{tools}

# REPLY
{reply}

# RUBRIC
{rubric}

Respond with only a JSON object: {{"score": <1-5>, "reasoning": "<one or two sentences>"}}"""

# The judge's score and its reason. A score of 0 means its output couldn't be parsed.
@dataclass
class Judgement:
    score: int
    reasoning: str


# Pull the JSON out of the reply, keeping valid scores within 1 to 5.
def parse_judgement(text: str) -> Judgement:
    match = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not match:
        return Judgement(0, f"Unparseable judge output: {text!r}")
    try:
        data = json.loads(match.group(0))
        score = int(data["score"])
    except (ValueError, KeyError, TypeError):
        return Judgement(0, f"Unparseable judge output: {text!r}")
    return Judgement(max(1, min(5, score)), str(data.get("reasoning", "")))


# Scores replies using a second model call. `reference` is the twin's system prompt.
class Judge:
    def __init__(self, llm: LLMClient, reference: str):
        self.llm = llm
        self.reference = reference

    def score(self, *, message: str, reply: str, rubric: str, history=None, approved=None, tool_calls=None) -> Judgement:
        history_text = "\n".join(f"{m['role']}: {m['content']}" for m in history or []) or "(none)"
        approved_text = "\n".join(f"Q: {m.question}\nA: {m.answer}" for m in approved or []) or "(none)"
        # Include each call's status, because a rejected call didn't record anything.
        tools_text = "\n".join(f"{c.name}: status {c.result.get('status')}" for c in tool_calls or []) or "(none)"
        prompt = JUDGE_PROMPT.format(
            reference=self.reference, approved=approved_text, history=history_text,
            message=message, tools=tools_text, reply=reply, rubric=rubric.strip(),
        )
        out = self.llm.complete([{"role": "user", "content": prompt}], None)
        return parse_judgement(out.content)