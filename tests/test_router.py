"""Router tests: pure function table."""

from sk.config import TIERS, provider_tier, resolve_alias
from sk.router import FAST_MODEL, SMART_MODEL, pick_model, pick_tier, should_escalate


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


def test_lone_generic_hit_stays_fast():
    # refs #275: single generic verbs are chit-chat until paired.
    assert pick_tier("write me a poem")[0] == "fast"
    assert pick_tier("run that thing")[0] == "fast"
    assert pick_tier("create something nice today")[0] == "fast"
    assert pick_tier("explain recursion")[0] == "fast"


def test_paired_hits_go_smart():
    assert pick_tier("write a backup script")[0] == "smart"
    assert pick_tier("run the tests")[0] == "smart"
    assert pick_tier("delete the temp folder")[0] == "smart"
    assert pick_tier("fix it now please")[0] == "smart"


def test_should_escalate_marks_failed_turns():
    assert should_escalate("") is True
    assert should_escalate("   ") is True
    assert should_escalate("(max steps reached)") is True
    assert should_escalate("Accomplished: x\nBlocked: y\nNext: z") is True
    assert should_escalate("bench-ok") is False
    assert should_escalate("Hey! What are we working on?") is False
    assert should_escalate("Denied: plan denied by user — nothing was executed.") is False
