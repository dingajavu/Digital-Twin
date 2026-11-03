"""Evaluation harness

Every run uses an isolated database for a sandbox environment evalualtion stage
Evaluation runs never touch produuction data.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import yaml
from dotenv import load_dotenv

from twin import KnowledgeBase, Twin, load_config
from twin.llm import LLMClient, build_client
from twin.notifications import NullNotifier

from .checks import CheckResult, run_checks
from .judge import Judge

# Reports from each run go in results/. The baseline is what later runs are compared against.
RESULTS_DIR = Path(__file__).parent/"results"
DEFAULT_BASELINE = Path(__file__).parent/"baseline.json"


# The result of running one case once.
@dataclass
class CaseResult:
    id: str
    category: str
    run: str
    input: str
    reply: str
    passed:bool
    checks: list[CheckResult]
    tools_used: list[str]
    kb_scores: list[float]
    latency_ms: int
    judge_score: int | None = None
    judge_reasoning: str | None = None
    error: str | None = None

# All results from one run of the harness.
@dataclass
class EvalRun:
    started_at: str
    model: str
    judge_model: str | None
    cases: list[CaseResult] = field(default_factory=list)

    def summary(self) -> dict:
        by_cat: dict[str, list[CaseResult]] = defaultdict(list)
        for c in self.cases:
            by_cat[c.category].append(c)

        def stats(rows: list[CaseResult]) -> dict:
            # Leave out cases with no judge score, or with one that couldn't be parsed.
            scores = [r.judge_score for r in rows if r.judge_score]
            return {
                "cases": len(rows),
                "pass_rate": round(sum(r.passed for r in rows) / len(rows), 3) if rows else 0.0,
                "mean_judge_score": round(sum(scores) / len(scores), 2) if scores else None,
                "mean_latency_ms": int(sum(r.latency_ms for r in rows) / len(rows)) if rows else 0,
            }

        return {"overall": stats(self.cases), "by_category": {k: stats(v) for k, v in sorted(by_cat.items())}}

# Load the cases, rejecting duplicate ids because they would muddle the reports.
def load_dataset(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    ids = [c["id"] for c in data["cases"]]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise ValueError(f"Duplicate case ids: {dupes}")
    return data

# A twin with an in-memory database and a silent notifier, so evals can't touch real data.
def build_eval_twin(cfg, dataset: dict, llm: LLMClient | None = None, source_texts=None) -> Twin:
    kb = KnowledgeBase(":memory:")
    for fx in dataset.get("kb_fixtures", []):
        kb.add_qa(fx["question"], fx["answer"], source="eval-fixture")
    return Twin.from_config(cfg, llm=llm, kb=kb, notifier=NullNotifier(), source_texts=source_texts)


# Run one case: get a reply, run the automatic checks, then ask the judge if there is one.
def run_case(twin: Twin, case: dict, rubric: str | None, judge: Judge | None, threshold: int, run: int) -> CaseResult:
    history = case.get("history", [])
    try:
        trace = twin.respond(case["input"], history)
    except Exception as exc:  # noqa: BLE001 - one broken case shouldn't stop the run
        return CaseResult(case["id"], case["category"], run, case["input"], "", False,
                          [], [], [], 0, error=f"{type(exc).__name__}: {exc}")

    checks = run_checks(case.get("expect", {}), trace)
    passed = all(c.passed for c in checks)
    result = CaseResult(
        id=case["id"], category=case["category"], run=run, input=case["input"], reply=trace.response,
        passed=passed, checks=checks, tools_used=trace.tools_used,
        kb_scores=[m.score for m in trace.kb_matches], latency_ms=trace.latency_ms,
    )
    if judge and rubric:
        j = judge.score(message=case["input"], reply=trace.response, rubric=rubric,
                        history=history, approved=trace.kb_matches, tool_calls=trace.tool_calls)
        result.judge_score, result.judge_reasoning = j.score, j.reasoning
        # A case needs both the automatic checks and a good enough score to pass.
        result.passed = passed and j.score >= threshold
    return result


def compare_to_baseline(summary: dict, baseline: dict, tolerance: float) -> list[str]:
    """Return a list of regressions (pass rate or judge score dropped beyond tolerance)."""
    problems = []
    for cat, cur in summary["by_category"].items():
        base = baseline.get("by_category", {}).get(cat)
        if not base:
            continue
        if cur["pass_rate"] < base["pass_rate"] - tolerance:
            problems.append(f"{cat}: pass rate {base['pass_rate']:.0%} -> {cur['pass_rate']:.0%}")
        b, c = base.get("mean_judge_score"), cur.get("mean_judge_score")
        if b and c and c < b - 0.5:
            problems.append(f"{cat}: judge score {b} -> {c}")
    return problems


# Build the Markdown report, listing overall results and then the details of each failure.
def render_report(run: EvalRun, regressions: list[str] | None) -> str:
    s = run.summary()
    o = s["overall"]
    lines = [
        f"# Eval report — {run.started_at}",
        f"Model: `{run.model}` · Judge: `{run.judge_model or 'disabled'}`",
        "",
        f"**Overall: {o['pass_rate']:.0%} pass** ({o['cases']} runs)"
        + (f" · mean judge score {o['mean_judge_score']}" if o["mean_judge_score"] else "")
        + f" · mean latency {o['mean_latency_ms']} ms",
        "",
        "| Category | Runs | Pass rate | Judge score | Latency (ms) |",
        "|---|---|---|---|---|",
    ]
    for cat, c in s["by_category"].items():
        lines.append(f"| {cat} | {c['cases']} | {c['pass_rate']:.0%} | {c['mean_judge_score'] or '-'} | {c['mean_latency_ms']} |")

    failures = [c for c in run.cases if not c.passed]
    if failures:
        lines += ["", "## Failures"]
        for f in failures:
            lines.append(f"\n### {f.id} (run {f.run})\n**Input:** {f.input}\n\n**Reply:** {f.reply or '(none)'}")
            if f.error:
                lines.append(f"\n**Error:** {f.error}")
            for ch in f.checks:
                if not ch.passed:
                    lines.append(f"- ❌ `{ch.name}` — {ch.detail}")
            if f.judge_score is not None:
                lines.append(f"- Judge {f.judge_score}/5 — {f.judge_reasoning}")
    if regressions is not None:
        lines += ["", "## Baseline comparison"]
        lines += [f"- ⚠️ {r}" for r in regressions] or ["- No regressions."]
    return "\n".join(lines)


# Command-line entry point. Returns 1 if a regression was found, so CI can detect it.
def main(argv: list[str] | None = None) -> int:
    load_dotenv(override=True)
    ap = argparse.ArgumentParser(description="Run the digital twin evaluation suite")
    ap.add_argument("--config")
    ap.add_argument("--dataset")
    ap.add_argument("--category", action="append", help="Only run these categories (repeatable)")
    ap.add_argument("--case", action="append", help="Only run these case ids (repeatable)")
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--no-judge", action="store_true")
    ap.add_argument("--baseline", type=Path, help="Compare against this baseline; exit 1 on regression")
    ap.add_argument("--tolerance", type=float, default=0.1, help="Allowed pass-rate drop per category")
    ap.add_argument("--save-baseline", action="store_true")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    dataset = load_dataset(cfg.resolve(args.dataset or cfg.evaluation.dataset))
    # --category and --case narrow the run to part of the dataset.
    cases = [c for c in dataset["cases"]
             if (not args.category or c["category"] in args.category)
             and (not args.case or c["id"] in args.case)]
    if not cases:
        print("No cases selected.")
        return 1

    twin = build_eval_twin(cfg, dataset)
    judge = None
    if not args.no_judge:
        judge_llm = build_client(cfg.llm, model=cfg.evaluation.judge_model or cfg.llm.model)
        judge = Judge(judge_llm, reference=twin.system_prompt)

    run = EvalRun(datetime.now().isoformat(timespec="seconds"), cfg.llm.model,
                  None if args.no_judge else (cfg.evaluation.judge_model or cfg.llm.model))
    rubrics = dataset.get("rubrics", {})
    total = len(cases) * args.repeats
    for r in range(1, args.repeats + 1):
        for case in cases:
            t0 = time.perf_counter()
            res = run_case(twin, case, case.get("rubric") or rubrics.get(case["category"]),
                           judge, cfg.evaluation.pass_threshold, r)
            run.cases.append(res)
            mark = "PASS" if res.passed else "FAIL"
            score = f" judge={res.judge_score}" if res.judge_score is not None else ""
            print(f"[{len(run.cases):>3}/{total}] {mark} {case['id']}{score} ({time.perf_counter() - t0:.1f}s)", flush=True)

    summary = run.summary()
    regressions = None
    # Compare against the saved baseline, unless this run is about to replace it.
    baseline_path = args.baseline or (DEFAULT_BASELINE if DEFAULT_BASELINE.exists() and not args.save_baseline else None)
    if baseline_path and not baseline_path.exists():
        print(f"No baseline at {baseline_path}; skipping comparison. Create one with --save-baseline.")
        baseline_path = None
    if baseline_path:
        regressions = compare_to_baseline(summary, json.loads(baseline_path.read_text()), args.tolerance)

    # Save the raw results and a readable report for each run.
    RESULTS_DIR.mkdir(exist_ok=True)
    stamp = run.started_at.replace(":", "-")
    (RESULTS_DIR / f"{stamp}.json").write_text(
        json.dumps({"summary": summary, "cases": [asdict(c) for c in run.cases]}, indent=2), encoding="utf-8")
    report = render_report(run, regressions)
    (RESULTS_DIR / f"{stamp}.md").write_text(report, encoding="utf-8")
    print("\n" + report)

    # Saves this run's scores as they are, so check the report before relying on them.
    if args.save_baseline:
        DEFAULT_BASELINE.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"\nSaved baseline to {DEFAULT_BASELINE}")
    return 1 if regressions else 0


if __name__ == "__main__":
    sys.exit(main())
