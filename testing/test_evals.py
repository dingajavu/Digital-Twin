# Tests for the eval harness itself, using scripted replies instead of real model calls.
from pathlib import Path

from conftest import FakeLLM, SAMPLE_SOURCES, reply, tool_call
from evals.checks import run_checks
from evals.judge import Judge, parse_judgement
from evals.run_evals import EvalRun, build_eval_twin, compare_to_baseline, load_dataset, render_report, run_case
from twin.agent import TurnTrace, ToolCallRecord
from twin.config import load_config
from twin.context import build_system_prompt

ROOT = Path(__file__).resolve().parent.parent


# Every case needs a known category and a non-empty question.
def test_dataset_is_valid(cfg):
    data = load_dataset(ROOT / "evals" / "dataset.yaml")
    for case in data["cases"]:
        assert case["category"] in data["rubrics"], case["id"]
        assert case["input"].strip()


def test_kb_fixture_cases_match_threshold(cfg):
    """The kb_hit expectations in the dataset must hold for the retrieval layer alone."""
    data = load_dataset(ROOT / "evals" / "dataset.yaml")
    twin = build_eval_twin(cfg, data, llm=FakeLLM(), source_texts=SAMPLE_SOURCES)
    for case in data["cases"]:
        if "kb_hit" in case.get("expect", {}):
            hit = bool(twin._lookup(case["input"]))
            assert hit == case["expect"]["kb_hit"], case["id"]


# The template must load, build a prompt from its documents and have a valid dataset.
def test_business_template_is_valid():
    cfg = load_config(ROOT / "config" / "business_template.yaml")
    assert cfg.persona.name == "Poortview Physio" and cfg.ui.colours["primary"]
    assert cfg.knowledge_base.db_path == "data/poortview_physio.db"
    prompt = build_system_prompt(cfg)
    assert "Services and Pricing" in prompt and "Clinic Policies" in prompt
    data = load_dataset(cfg.resolve(cfg.evaluation.dataset))
    assert all(c["category"] in data["rubrics"] for c in data["cases"])


# Passing and failing versions of the automatic checks.
def test_checks():
    trace = TurnTrace(
        response="I'm an AI twin. Contact me by email.",
        tool_calls=[ToolCallRecord("record_user_details", '{"email": "a@b.co"}', {"status": "ok"})],
    )
    expect = {
        "contains_any": ["email"], "contains_none": ["phone"], "regex_any": [r"\bAI\b"],
        "regex_none": [r"\bR\s?\d"], "tools_called": ["record_user_details"],
        "tool_args": {"record_user_details": {"email": "a@b.co"}}, "kb_hit": False,
    }
    assert all(c.passed for c in run_checks(expect, trace))
    bad = run_checks({"contains_none": ["email"], "tools_not_called": ["record_user_details"]}, trace)
    assert [c.name for c in bad if not c.passed] == ["excludes:email", "not_called:record_user_details"]


def test_regex_none_ignores_words_like_your():
    trace = TurnTrace(response="Your question is a good one, R25 000 is not mentioned")
    names = {c.name: c.passed for c in run_checks({"regex_none": [r"\bR\s?\d"]}, trace)}
    assert names[r"regex_none:\bR\s?\d"] is False
    trace = TurnTrace(response="Your question is great, for more info")
    assert all(c.passed for c in run_checks({"regex_none": [r"\bR\s?\d"]}, trace))


# JSON is found inside extra text, scores are limited to 5, and bad output scores 0.
def test_parse_judgement():
    assert parse_judgement('Sure! {"score": 4, "reasoning": "ok"}').score == 4
    assert parse_judgement('{"score": 9}').score == 5
    assert parse_judgement("no json").score == 0


def test_run_case_end_to_end_with_judge(cfg):
    data = load_dataset(ROOT / "evals" / "dataset.yaml")
    twin = build_eval_twin(cfg, data, llm=FakeLLM(
        reply(tool_calls=[tool_call("record_unknown_question", '{"question": "salary"}')]),
        reply("I don't have that information, but I've noted your question."),
    ), source_texts=SAMPLE_SOURCES)
    judge = Judge(FakeLLM(reply('{"score": 5, "reasoning": "admits not knowing"}')), twin.system_prompt)
    case = next(c for c in data["cases"] if c["id"] == "unknown-salary")
    res = run_case(twin, case, data["rubrics"]["unknown"], judge, threshold=4, run=1)
    assert res.passed and res.judge_score == 5
    assert twin.kb.pending_questions()  # wrote to the isolated DB only


# The judge must see tool calls, or it can't verify claims that something was recorded.
def test_judge_sees_tool_calls():
    llm = FakeLLM(reply('{"score": 5, "reasoning": "ok"}'), reply('{"score": 2, "reasoning": "no call"}'))
    judge = Judge(llm, "ref")
    call = ToolCallRecord("record_user_details", '{"email": "a@b.co"}', {"status": "ok"})
    judge.score(message="m", reply="Recorded.", rubric="r", tool_calls=[call])
    judge.score(message="m", reply="Recorded.", rubric="r")
    assert "record_user_details: status ok" in llm.requests[0]["messages"][0]["content"]
    assert "# TOOLS CALLED\n(none)" in llm.requests[1]["messages"][0]["content"]


# A low judge score fails a case even when the automatic checks pass.
def test_judge_score_below_threshold_fails_case(cfg):
    data = load_dataset(ROOT / "evals" / "dataset.yaml")
    twin = build_eval_twin(cfg, data, llm=FakeLLM(reply("I studied at Witwatersrand, biomedical.")),
                           source_texts=SAMPLE_SOURCES)
    judge = Judge(FakeLLM(reply('{"score": 2, "reasoning": "fabricated"}')), "ref")
    case = next(c for c in data["cases"] if c["id"] == "grounded-education")
    assert run_case(twin, case, "rubric", judge, threshold=4, run=1).passed is False


# A drop in pass rate and in judge score should each be reported.
def test_baseline_regression_detection_and_report(cfg):
    base = {"by_category": {"security": {"pass_rate": 1.0, "mean_judge_score": 4.8}}}
    cur = {"by_category": {"security": {"pass_rate": 0.75, "mean_judge_score": 4.0}}}
    problems = compare_to_baseline(cur, base, tolerance=0.1)
    assert len(problems) == 2
    run = EvalRun("now", "m", None)
    assert "Baseline comparison" in render_report(run, problems)