"""Image input tests (fixes #178): refs, vision gating, prompt parts,
Anthropic translation. Fully offline (tiny embedded PNG)."""

import base64

import sk.images as images
from sk.config import Config

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _cfg(**kw):
    base = {
        "provider": "ollama",
        "model": "qwen2.5-vl:7b",
        "base_url": "http://x/v1",
        "api_key": "x",
        "max_steps": 1,
        "temperature": 0.0,
    }
    base.update(kw)
    return Config(**base)


def _iso(tmp_path, monkeypatch):
    import sk.config as config_mod
    import sk.store as store

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path / ".sidekick")
    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / ".sidekick" / "config.toml")
    monkeypatch.chdir(tmp_path)


def _png(tmp_path, name="shot.png"):
    p = tmp_path / name
    p.write_bytes(PNG)
    return p


def test_vision_capable_matrix():
    assert images.vision_capable("ollama", "qwen2.5-vl:7b") is True
    assert images.vision_capable("ollama", "llava:13b") is True
    assert images.vision_capable("openai", "gpt-4o-mini") is True
    assert images.vision_capable("anthropic", "claude-sonnet-5") is True
    assert images.vision_capable("ollama", "llama3.2:3b") is False
    assert images.vision_capable("ollama", "qwen2.5-coder:7b") is False
    assert images.vision_capable("ollama", "") is False
    assert images.is_image_path("a.PNG") is True
    assert images.is_image_path("a.md") is False


def test_extract_refs(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    _png(tmp_path)
    (tmp_path / "notes.md").write_text("hi")
    cleaned, paths = images.extract_image_refs("what is wrong here @shot.png and @notes.md?")
    assert cleaned.count("[attached image: shot.png]") == 1
    assert "@notes.md" in cleaned  # text refs untouched
    assert paths == [str((tmp_path / "shot.png").resolve())]
    cleaned, _ = images.extract_image_refs("look @missing.png")
    assert "image not found" in cleaned


def test_extract_oversize_note(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    big = tmp_path / "big.png"
    big.write_bytes(b"0" * (images.MAX_INPUT_BYTES + 1))
    cleaned, paths = images.extract_image_refs("see @big.png")
    assert "too large" in cleaned and paths == []


def test_encode_data_url(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    p = _png(tmp_path)
    url = images.encode_image_data_url(str(p))
    assert url.startswith("data:image/png;base64,")
    assert base64.b64decode(url.split(",", 1)[1]) == PNG
    assert images.encode_image_data_url(str(tmp_path / "nope.png")) is None
    (tmp_path / "t.txt").write_text("x")
    assert images.encode_image_data_url(str(tmp_path / "t.txt")) is None


def test_prompt_parts_for_vision_model(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    from sk.agent import build_messages

    _png(tmp_path)
    msgs = build_messages("what is wrong here @shot.png", [], _cfg())
    content = msgs[-1]["content"]
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": content[0]["text"]}
    assert "[attached image: shot.png]" in content[0]["text"]
    img = content[1]
    assert img["type"] == "image_url"
    assert img["image_url"]["url"].startswith("data:image/png;base64,")


def test_prompt_note_for_text_model(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    from sk.agent import build_messages

    _png(tmp_path)
    msgs = build_messages("look @shot.png", [], _cfg(model="llama3.2:3b"))
    content = msgs[-1]["content"]
    assert isinstance(content, str) and "has no vision support" in content


def test_prompt_unchanged_without_refs(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    from sk.agent import build_messages

    msgs = build_messages("hello there", [], _cfg())
    assert msgs[-1]["content"] == "hello there"


def test_anthropic_image_translation():
    import sk.anthropic_backend as ab

    msgs = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "look"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}},
            ],
        }
    ]
    system, out = ab.openai_messages_to_anthropic(msgs)
    img = out[0]["content"][1]
    assert img["type"] == "image"
    assert img["source"] == {"type": "base64", "media_type": "image/png", "data": "QUJD"}
    msgs[0]["content"][1] = {"type": "image_url", "image_url": {"url": "https://x/y.png"}}
    _, out = ab.openai_messages_to_anthropic(msgs)
    assert "not supported" in out[0]["content"][1]["text"]
