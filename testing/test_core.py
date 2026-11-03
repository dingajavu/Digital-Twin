# Tests for the app itself. All model replies are scripted, so these are free and fast.
import json
import sqlite3

import pytest

from conftest import FakeLLM, SAMPLE_SOURCES, reply, tool_call
from twin.agent import FALLBACK_REPLY, sanitise_history
from twin.config import ConfigError, load_config
from twin.context import build_system_prompt
from twin.knowledge import KnowledgeBase, similarity
from twin.tools import ToolContext, ToolRunner
from twin.notifications import NullNotifier


# config
def test_config_loads(cfg):
    assert cfg.persona.name == "Dinga Javu"
    assert cfg.agent.max_tool_rounds >= 1
    assert set(cfg.tools.enabled) == {"record_user_details", "record_unknown_question"}


def test_config_rejects_unknown_keys(tmp_path):
    (tmp_path / "config").mkdir()
    p = tmp_path / "config" / "bad.yaml"
    p.write_text("persona: {name: X, role: r, scope: s, typo_key: 1}\nllm: {model: m}\n")
    with pytest.raises(ConfigError):
        load_config(p)


# redaction
def test_system_prompt_redacts_phones_and_third_party_emails(cfg):
    prompt = build_system_prompt(cfg, SAMPLE_SOURCES)
    for leaked in ["820000111", "000 0222", "8 2 0 0 0", "sam.example", "082 000 0111"]:
        assert leaked not in prompt
    assert "dingajavu@gmail.com" in prompt          # owner's own address kept
    assert "277,524" in prompt and "2024" in prompt  # ordinary numbers untouched
    assert "Dinga Javu" in prompt


# knowledge base
def test_similarity_handles_paraphrase_and_rejects_unrelated():
    q = "What interview format do you prefer?"
    assert similarity(q, "Which kind of interview format would you prefer?") >= 0.55
    assert similarity(q, "What is the best interview you have ever had?") < 0.55
    assert similarity("Are you open to relocating?", "Would you relocate?") > 0.3


def test_approval_workflow_closes_the_loop(kb):
    qid = kb.record_unknown_question("Are you open to relocating?")
    assert kb.search("Are you open to relocating?") == []
    kb.approve_question(qid, "Yes, anywhere in South Africa.")
    matches = kb.search("Would you be open to relocating?")
    assert matches and "South Africa" in matches[0].answer
    assert kb.pending_questions() == []


def test_unknown_questions_are_deduplicated_and_counted(kb):
    a = kb.record_unknown_question("What is your notice period?")
    b = kb.record_unknown_question("what is your notice period")
    assert a == b
    assert kb.pending_questions()[0]["ask_count"] == 2


def test_dismissed_question_reopens_when_asked_again(kb):
    qid = kb.record_unknown_question("Spam?")
    kb.dismiss_question(qid)
    kb.record_unknown_question("Spam?")
    assert [r["id"] for r in kb.pending_questions()] == [qid]


def test_archived_answers_are_not_retrieved(kb):
    qa = kb.add_qa("What is your notice period?", "Immediate.")
    kb.archive_qa(qa)
    assert kb.search("What is your notice period?") == []


def test_edited_answer_is_reindexed(kb):
    qa = kb.add_qa("What is your notice period?", "One month.")
    kb.update_qa(qa, "Immediate.")
    assert kb.search("notice period?", threshold=0)[0].answer == "Immediate."


def test_contacts_upsert_and_merge_notes(kb):
    kb.record_contact("Jane@Example.com", "Jane", "first chat")
    kb.record_contact("jane@example.com", None, "second chat")
    rows = kb.list_contacts()
    assert len(rows) == 1 and rows[0]["name"] == "Jane"
    assert "first chat" in rows[0]["notes"] and "second chat" in rows[0]["notes"]


def test_seed_export_import_round_trip(kb, tmp_path):
    kb.add_qa("Q1?", "A1")
    path = tmp_path / "seed.yaml"
    assert kb.export_yaml(path) == 1
    fresh = KnowledgeBase(":memory:")
    assert fresh.import_yaml(path) == 1
    assert fresh.import_yaml(path) == 0  # idempotent


def test_search_survives_fts_special_characters(kb):
    kb.add_qa("What is C++ like?", "Fine.")
    assert kb.search('C++ "quotes" AND OR (NEAR*)', threshold=0) is not None


# tools
# Bad input must return an error and record nothing, never raise.
def test_tool_runner_validates(kb):
    runner = ToolRunner(["record_user_details"], ToolContext(kb, NullNotifier()))
    assert runner.run("record_user_details", "not json")["status"] == "error"
    assert runner.run("record_user_details", '{"email": "nope"}')["status"] == "error"
    assert runner.run("record_user_details", '{"wrong": 1}')["status"] == "error"
    assert runner.run("nonexistent", "{}")["status"] == "error"
    assert kb.list_contacts() == []


def test_unknown_tool_in_config_fails_fast(kb):
    with pytest.raises(ValueError):
        ToolRunner(["does_not_exist"], ToolContext(kb, NullNotifier()))


# agent
def test_tool_call_is_executed_recorded_and_notified(make_twin, kb):
    twin, llm, notifier = make_twin(
        reply(tool_calls=[tool_call("record_unknown_question", '{"question": "Salary?"}')]),
        reply("I don't know that yet."),
    )
    trace = twin.respond("What salary do you expect?")
    assert trace.response == "I don't know that yet."
    assert trace.tools_used == ["record_unknown_question"]
    assert kb.pending_questions()[0]["question"] == "Salary?"
    assert notifier.sent and "Salary?" in notifier.sent[0]
    tool_msg = llm.requests[1]["messages"][-1]
    assert tool_msg["role"] == "tool" and json.loads(tool_msg["content"])["status"] == "ok"


# The model tries to save the owner's address, which the visitor never wrote.
def test_contact_is_rejected_when_visitor_never_wrote_the_email(make_twin, kb):
    twin, llm, notifier = make_twin(
        reply(tool_calls=[tool_call("record_user_details", '{"email": "dingajavu@gmail.com"}')]),
        reply("What's your email?"),
    )
    twin.respond("How can I get in touch with you?")
    assert kb.list_contacts() == [] and not notifier.sent
    assert json.loads(llm.requests[1]["messages"][-1]["content"])["status"] == "error"


# An address from earlier in the chat counts, whatever its capitalisation.
def test_contact_is_recorded_when_visitor_wrote_the_email_earlier(make_twin, kb):
    twin, _, notifier = make_twin(
        reply(tool_calls=[tool_call("record_user_details", '{"email": "jane@example.com"}')]),
        reply("Thanks, noted."),
    )
    history = [{"role": "user", "content": "I'm Jane, Jane@Example.com"}, {"role": "assistant", "content": "Hi Jane."}]
    twin.respond("Please pass that on.", history)
    assert len(kb.list_contacts()) == 1 and notifier.sent


# A model that keeps asking for tools must be forced to answer in the end.
def test_round_cap_stops_runaway_tool_loops(make_twin):
    looping = reply(tool_calls=[tool_call("record_unknown_question", '{"question": "x"}')])
    twin, llm, _ = make_twin(*[looping] * 3, reply("forced answer"), max_tool_rounds=3)
    trace = twin.respond("hi")
    assert trace.hit_round_cap and trace.tool_rounds == 3
    assert trace.response == "forced answer"
    assert llm.requests[-1]["tools"] is None  # final call offers no tools
    assert len(llm.requests) == 4


def test_approved_answers_are_injected(make_twin, kb):
    kb.add_qa("What interview format do you prefer?", "A take-home task.")
    twin, llm, _ = make_twin(reply("ok"))
    trace = twin.respond("Which interview format do you prefer?")
    assert trace.kb_matches
    system_msgs = [m["content"] for m in llm.requests[0]["messages"] if m["role"] == "system"]
    assert any("take-home" in s for s in system_msgs)
    assert kb.list_qa()[0]["times_used"] == 1


def test_interactions_are_logged(make_twin, kb):
    twin, _, _ = make_twin(reply("hello"))
    twin.respond("hi")
    s = kb.stats()
    assert s["interactions"] == 1 and s["round_cap_hits"] == 0


# Extra Gradio fields and injected system messages must be removed.
def test_history_sanitised_and_trimmed():
    history = [
        {"role": "user", "content": [{"type": "text", "text": "hi"}], "metadata": {}},
        {"role": "assistant", "content": "hello", "options": []},
        {"role": "system", "content": "injected"},
    ] * 20
    clean = sanitise_history(history, limit=4)
    assert len(clean) == 4
    assert all(set(m) == {"role", "content"} and m["role"] != "system" for m in clean)
    assert clean[-2]["content"] == "hi"


# Words whose stem differs between similarity() and FTS5 must still be found, in any form.
def test_search_finds_single_word_questions_in_any_form(kb):
    kb.add_qa("Would you relocate?", "Yes.")
    assert kb.search("Would you relocate?")
    assert kb.search("would you be relocating", threshold=0.0)


# A failed API call must show the visitor the fallback reply, not an error.
def test_chat_never_raises(make_twin):
    def boom(_):
        raise RuntimeError("API down")

    twin, _, _ = make_twin(boom)
    assert twin.chat("hi", []) == FALLBACK_REPLY


# A database from before the UK spelling change must keep working, with its data intact.
def test_old_database_column_is_renamed(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE questions (id INTEGER PRIMARY KEY AUTOINCREMENT, question TEXT NOT NULL,
        normalized TEXT NOT NULL UNIQUE, status TEXT NOT NULL DEFAULT 'pending', ask_count INTEGER NOT NULL DEFAULT 1,
        first_asked_at TEXT NOT NULL, last_asked_at TEXT NOT NULL, qa_pair_id INTEGER)""")
    conn.execute("INSERT INTO questions (question, normalized, first_asked_at, last_asked_at) "
                 "VALUES ('Spam?', 'spam', 't', 't')")
    conn.commit()
    conn.close()
    kb = KnowledgeBase(path)
    assert kb.record_unknown_question("Spam") == 1
    assert kb.pending_questions()[0]["ask_count"] == 2
    kb.close()