"""Deterministic checks: cheap, exact and repeatable. Run before the LLM judge."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from twin.agent import TurnTrace


# The outcome of one check. `detail` explains a failure in the report.
@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""


# Turn a case's `expect` block into pass or fail results. Matching ignores case.
def run_checks(expect: dict, trace: TurnTrace) -> list[CheckResult]:
    reply = trace.response or ""
    low = reply.lower()
    used = trace.tools_used
    results: list[CheckResult] = []

    if words := expect.get("contains_any"):                                                                 # At least one of these strings appears in the reply
        hit = [w for w in words if str(w).lower() in low]
        results.append(CheckResult("contains_any", bool(hit), f"expected one of {words}"))
    for w in expect.get("contains_all", []):                                                                # All of these strings appear in the reply
        results.append(CheckResult(f"contains:{w}", str(w).lower() in low, f"missing '{w}'"))
    for w in expect.get("contains_none", []):                                                               # None of these strings appear in the reply - could be leaks or fabrications
        results.append(CheckResult(f"excludes:{w}", str(w).lower() not in low, f"reply contains '{w}'"))
    if patterns := expect.get("regex_any"):                                                                 # At least one of these regex patterns matches the reply
        hit = [p for p in patterns if re.search(p, reply, re.IGNORECASE)]
        results.append(CheckResult("regex_any", bool(hit), f"no match for any of {patterns}"))
    for p in expect.get("regex_none", []):                                                                  # None of these regex patterns match the reply - could be leaks or fabrications
        m = re.search(p, reply, re.IGNORECASE)
        results.append(CheckResult(f"regex_none:{p}", m is None, f"matched '{m.group(0)}'" if m else ""))

    for t in expect.get("tools_called", []):                                                                # Tools that should have been called by the agent
        results.append(CheckResult(f"called:{t}", t in used, f"tools used: {used}"))
    for t in expect.get("tools_not_called", []):                                                            # Tools that should NOT have been called by the agent
        results.append(CheckResult(f"not_called:{t}", t not in used, f"tools used: {used}"))
    for t, args in (expect.get("tool_args") or {}).items():                                                 # Check that the tool was called with the expected arguments
        calls = [json.loads(c.arguments or "{}") for c in trace.tool_calls if c.name == t]
        for arg, want in args.items():
            ok = any(str(want).lower() in str(c.get(arg, "")).lower() for c in calls)
            results.append(CheckResult(f"arg:{t}.{arg}", ok, f"calls: {calls}"))

    if "kb_hit" in expect:                                                                                   # Check whether an approved answer was retireved   
        hit = bool(trace.kb_matches)
        scores = [m.score for m in trace.kb_matches]
        results.append(CheckResult("kb_hit", hit == expect["kb_hit"], f"kb scores: {scores}"))

    # These two checks apply to every case.
    results.append(CheckResult("no_round_cap", not trace.hit_round_cap, "agent hit the tool round cap"))
    results.append(CheckResult("non_empty", bool(reply.strip()), "empty reply"))
    return results