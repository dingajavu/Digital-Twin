# Shared test setup: fake model replies and fixtures, so tests make no real API calls.
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
# Let tests import the project's packages when run from any folder.
sys.path.insert(0, str(ROOT))

from twin import KnowledgeBase, Twin, load_config  # noqa: E402
from twin.notifications import NullNotifier  # noqa: E402

# Fake details in the same formats as the real CV, to prove redaction works.
SAMPLE_SOURCES = {
    "Personal Summary": "My name is Dinga Javu. I played rugby through university.",
    "Career Summary, CV, Cover Letter and LinkedIn Profile": (
        "DINGA JAVU\nJohannesburg, GP | +27820000111 | dingajavu@gmail.com\n"
        "Skilled in Python, AWS and LangChain.\n"
        "References\nThandi Example\nTel: +27 83 000 0222\nDr. Sam Example\nsam.example@example.com\n"
        "J o h a n n e s b u r g | + 2 7 8 2 0 0 0 0 1 1 1 | d i n g a j a v u\n"
        "Trained on 277,524 tissue patches between 2024 and 2025. Alt: 082 000 0111"
    ),
    "Project Facts": "Sample project: a classifier built with Python.",
    "Project READMEs from GitHub": "Sample README: how the classifier was trained.",
}


# A fake tool request, shaped like the OpenAI response.
def tool_call(name, arguments, id_="call_1"):
    return SimpleNamespace(id=id_, function=SimpleNamespace(name=name, arguments=arguments))


# A fake model reply, shaped like the OpenAI response.
def reply(content=None, tool_calls=None):
    return SimpleNamespace(content=content, tool_calls=tool_calls or None)


class FakeLLM:
    """Returns scripted replies in order and records every request."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests = []

    def complete(self, messages, tools=None):
        self.requests.append({"messages": [dict(m) for m in messages], "tools": tools})
        # Scripted replies ran out, so return a harmless default.
        if not self.replies:
            return reply("default answer")
        r = self.replies.pop(0)
        # A callable reply can raise an error, for testing failures.
        return r(messages) if callable(r) else r


@pytest.fixture
# The real config, so tests also catch mistakes in it.
def cfg():
    return load_config(ROOT / "config" / "twin.yaml")


@pytest.fixture
# A fresh in-memory database for each test.
def kb():
    k = KnowledgeBase(":memory:")
    yield k
    k.close()


@pytest.fixture
# Builds a twin with scripted replies, returning it with the fake model and notifier.
def make_twin(cfg, kb):
    def _make(*replies, **overrides):
        llm = FakeLLM(*replies)
        notifier = NullNotifier()
        twin = Twin.from_config(cfg, llm=llm, kb=kb, notifier=notifier, source_texts=SAMPLE_SOURCES)
        for k, v in overrides.items():
            setattr(twin.cfg.agent, k, v)
        return twin, llm, notifier

    return _make