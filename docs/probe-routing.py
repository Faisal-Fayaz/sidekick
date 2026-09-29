"""Routing probe for #200: does stealth/space-bunny-alpha obey system prompts?

Run with your own key (spends a few cents at most):
    SIDEKICK_API_KEY=sk-or-... python3 docs/probe-routing.py

Sends one fixed instruction three ways and reports which forms the model
obeys vs echoes. Paste the verdict table into issue #200.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request

MODEL = "stealth/space-bunny-alpha"
URL = "https://openrouter.ai/api/v1/chat/completions"
INSTRUCTION = "Reply with ONLY the exact string: routing-probe-ok"


def _post(payload: dict, api_key: str) -> dict:
    req = urllib.request.Request(
        URL,
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def _verdict(text: str) -> str:
    t = (text or "").strip()
    if t == "routing-probe-ok":
        return "OBEYED"
    if "routing-probe-ok" in t and len(t) < 200:
        return "PARAPHRASED"
    return "ECHOED/IGNORED"


def main() -> int:
    api_key = os.getenv("SIDEKICK_API_KEY", "").strip()
    if not api_key:
        print("Set SIDEKICK_API_KEY first.")
        return 2
    variants = {
        "system-role (current)": [
            {"role": "system", "content": INSTRUCTION},
            {"role": "user", "content": "Go."},
        ],
        "user-embedded": [
            {"role": "user", "content": INSTRUCTION + "\nGo."},
        ],
        "system-plus-suffix": [
            {"role": "system", "content": INSTRUCTION},
            {"role": "user", "content": "Go. Follow instructions literally."},
        ],
        "developer-role": [
            {"role": "developer", "content": INSTRUCTION},
            {"role": "user", "content": "Go."},
        ],
    }
    print(f"{'variant':<22} verdict")
    ok = True
    for name, messages in variants.items():
        try:
            resp = _post({"model": MODEL, "messages": messages, "max_tokens": 200}, api_key)
            text = ((resp.get("choices") or [{}])[0].get("message") or {}).get("content", "")
        except Exception as e:
            text = ""
            print(f"{name:<22} ERROR: {e}")
            ok = False
            continue
        print(f"{name:<22} {_verdict(text)} :: {text[:120]!r}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
