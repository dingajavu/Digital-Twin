# Swapping the config file must change the whole deployment and nothing must leak between them.
import json
from pathlib import Path

import pytest

from conftest import SAMPLE_SOURCES, FakeLLM, reply, tool_call
from evals.judge import Judge
from evals.run_evals import baseline_path_for
from twin import KnowledgeBase, Twin, load_config
from twin.config import ConfigError
from twin.context import build_system_prompt
from twin.notifications import NullNotifier

ROOT = Path(__file__).resolve().parent.parent
TWIN = ROOT / "config" / "twin.yaml"
BUSINESS = ROOT / "config" / "business_template.yaml"


# A complete assistant built from the clinic config, with scripted model replies.
def business_twin(*replies):
    cfg = load_config(BUSINESS)
    kb = KnowledgeBase(":memory:")
    llm = FakeLLM(*replies)
    return Twin.from_config(cfg, llm=llm, kb=kb, notifier=NullNotifier()), llm, kb


def test_business_config_runs_end_to_end():
    twin, llm, kb = business_twin(
        reply(tool_calls=[tool_call("record_unknown_question", '{"question": "Is there parking?"}')]),
        reply("A staff member will get back to you."),
    )
    trace = twin.respond("Is there parking?")
    assert trace.tools_used == ["record_unknown_question"]
    assert kb.pending_questions()[0]["question"] == "Is there parking?"
    # Nothing from the personal twin reaches the clinic's prompt.
    prompt = twin.system_prompt.lower()
    assert "poortview physio" in prompt
    for word in ("dinga", "career", "cv", "linkedin", "recruit"):
        assert word not in prompt, word


def test_approved_answers_intro_comes_from_config(cfg, kb):
    business, llm, bkb = business_twin(reply("ok"))
    bkb.add_qa("What are your opening hours?", "Monday to Friday, 08:00 to 17:00.")
    business.respond("What are your opening hours?")
    block = next(m["content"] for m in llm.requests[0]["messages"] if "# Approved answers" in m["content"])
    assert "approved by the Poortview Physio team" in block and "person you represent" not in block

    # The personal twin keeps its wording exactly as it was before it moved to the config.
    kb.add_qa("What interview format do you prefer?", "A take-home task.")
    llm2 = FakeLLM(reply("ok"))
    Twin.from_config(cfg, llm=llm2, kb=kb, notifier=NullNotifier(), source_texts=SAMPLE_SOURCES).respond(
        "Which interview format do you prefer?")
    block = next(m["content"] for m in llm2.requests[0]["messages"] if "# Approved answers" in m["content"])
    assert ("The following answers were written and approved by the person you represent. They are "
            "authoritative: prefer them over anything else, keep their facts unchanged, and adapt only "
            "the wording to fit the conversation.") in block


def test_judge_names_who_the_assistant_represents():
    llm = FakeLLM(reply('{"score": 5, "reasoning": "fine"}'))
    Judge(llm, "ref", subject="Poortview Physio").score(message="m", reply="r", rubric="x")
    prompt = llm.requests[0]["messages"][0]["content"]
    assert "represents Poortview Physio" in prompt and "about the person" not in prompt


def test_each_config_has_its_own_baseline():
    twin_path, business_path = baseline_path_for(load_config(TWIN)), baseline_path_for(load_config(BUSINESS))
    assert twin_path.name == "twin.json" and business_path.name == "business_template.json"
    assert twin_path != business_path


def test_saving_a_baseline_never_touches_another_config(tmp_path, monkeypatch):
    import evals.run_evals as run_evals

    monkeypatch.setattr(run_evals, "BASELINES_DIR", tmp_path)
    monkeypatch.setattr(run_evals, "RESULTS_DIR", tmp_path / "results")
    monkeypatch.setattr(run_evals, "build_client", lambda *a, **k: FakeLLM(*[reply("R850")] * 20))
    original = run_evals.build_eval_twin
    monkeypatch.setattr(run_evals, "build_eval_twin",
                        lambda cfg, ds, llm=None, source_texts=None: original(cfg, ds, llm=FakeLLM(*[reply("R850")] * 20)))
    (tmp_path / "twin.json").write_text(json.dumps({"by_category": {"unknown": {"pass_rate": 1.0}}}))

    run_evals.main(["--config", str(BUSINESS), "--no-judge", "--save-baseline"])
    assert json.loads((tmp_path / "twin.json").read_text()) == {"by_category": {"unknown": {"pass_rate": 1.0}}}
    assert (tmp_path / "business_template.json").exists()
    # The next clinic run compares only against the clinic's own baseline, so it finds no regression.
    # Compared with the twin's baseline, "unknown" would have dropped from 100% to 0%.
    assert run_evals.main(["--config", str(BUSINESS), "--no-judge"]) == 0


def test_light_and_dark_colours_apply_to_their_own_theme():
    import app

    css = app.themed_css(load_config(BUSINESS).ui.colours)
    overrides = css.split("/* Brand colours from the config */")[1]
    dark, light = overrides.split("body:not(.dark)")
    assert "--twin-primary: #1E90FF;" in dark
    assert "--twin-primary: #1565C0;" in light and "#1E90FF" not in light
    assert "--twin-secondary-ink: #14232b;" in light


@pytest.mark.parametrize("colours, message", [
    ({"primary": "#1E90FF"}, "split into 'dark' and 'light'"),
    ({"dark": {"primary": "blue"}}, "must be a hex colour"),
    ({"light": {"background": "#ffffff"}}, "Invalid 'ui' section"),
])
def test_bad_colours_fail_at_startup(tmp_path, colours, message):
    p = tmp_path / "config" / "c.yaml"
    p.parent.mkdir()
    p.write_text(json.dumps({"persona": {"name": "X", "role": "r", "scope": "s"},
                             "llm": {"model": "m"}, "ui": {"colours": colours}}))
    with pytest.raises(ConfigError, match=message):
        load_config(p)


# Braces other than {name} are ordinary text, so config authors can't crash startup with them.
def test_other_braces_in_config_text_are_kept(tmp_path):
    p = tmp_path / "config" / "c.yaml"
    p.parent.mkdir()
    p.write_text(json.dumps({
        "persona": {"name": "Clinic", "role": "You help visitors of {name}.", "scope": "s",
                    "rules": ["Quote prices as {currency} amounts.", "Reply as JSON like {\"ok\": true} if asked."]},
        "llm": {"model": "m"},
    }))
    prompt = build_system_prompt(load_config(p))
    assert "You help visitors of Clinic." in prompt
    assert "- Quote prices as {currency} amounts." in prompt
    assert '{"ok": true}' in prompt
