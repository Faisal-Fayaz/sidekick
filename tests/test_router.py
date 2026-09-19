"""Router tests: pure function table."""

from sk.router import FAST_MODEL, SMART_MODEL, pick_model


def test_chat_goes_fast():
    assert pick_model("say hi in 3 words")[0] == FAST_MODEL
    assert pick_model("thanks")[0] == FAST_MODEL


def test_code_goes_smart():
    assert pick_model("refactor this function")[0] == SMART_MODEL
    assert pick_model("debug the failing test")[0] == SMART_MODEL


def test_paths_go_smart():
    assert pick_model("check ~/ecomind scope")[0] == SMART_MODEL
    assert pick_model("read src/sk/agent.py")[0] == SMART_MODEL


def test_device_questions_go_smart():
    assert pick_model("what LLM can I run on my device")[0] == SMART_MODEL


def test_fetch_and_recall_go_fast():
    assert pick_model("fetch https://example.com and summarize")[0] == FAST_MODEL
    assert pick_model("what do you remember about models")[0] == FAST_MODEL


def test_empty_defaults_fast():
    assert pick_model("")[0] == FAST_MODEL
