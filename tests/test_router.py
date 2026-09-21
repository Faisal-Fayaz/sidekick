"""Router tests: pure function table."""

from sk.config import TIERS, provider_tier, resolve_alias
from sk.router import FAST_MODEL, SMART_MODEL, pick_model, pick_tier


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


def test_pick_tier_matches_pick_model():
    assert pick_tier("say hi in 3 words")[0] == "fast"
    assert pick_tier("refactor this function")[0] == "smart"
    assert pick_tier("")[0] == "fast"


def test_router_constants_follow_tiers():
    assert FAST_MODEL == TIERS["ollama"]["fast"]
    assert SMART_MODEL == TIERS["ollama"]["smart"]


def test_tier_resolves_per_provider():
    # tier decision is provider-agnostic; concrete names come from TIERS
    tier, _ = pick_tier("refactor this function")
    assert tier == "smart"
    assert provider_tier("ollama", tier, "d") == TIERS["ollama"]["smart"]
    assert provider_tier("groq", tier, "d") == TIERS["groq"]["smart"]
    assert provider_tier("openai", "fast", "d") == TIERS["openai"]["fast"]
    tier, _ = pick_tier("thanks")
    assert provider_tier("groq", tier, "d") == TIERS["groq"]["fast"]


def test_resolve_alias_shared_by_cli_and_slash():
    assert resolve_alias("ollama", "fast", "d") == TIERS["ollama"]["fast"]
    assert resolve_alias("groq", "smart", "d") == TIERS["groq"]["smart"]
    assert resolve_alias("custom", "smart", "mydefault") == "mydefault"
    assert resolve_alias("ollama", "my-model", "d") == "my-model"
