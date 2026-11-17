"""Creates local model variants in Ollama: same weights, a serving config that fits the agent.

Ollama runs models with a 4k context by default. That silently truncates the oldest part of a
longer booking conversation (the system prompt plus ten tool schemas alone are ~3k tokens), so
each variant raises ``num_ctx`` to 12k.

We deliberately keep each model's *stock* chat template. We tested moving Llama-3.1's tool schemas
from the newest user message into the (cacheable) system prompt: turns got ~2.5x faster, but the
model stopped calling tools and invented restaurants. See docs/PROMPT_ENGINEERING.md, experiment E3.

    python -m goodfoods.agent.ollama_setup
"""

from __future__ import annotations

import os
import re

import httpx

OLLAMA = os.getenv("OLLAMA_HOST_URL", "http://localhost:11434")
NUM_CTX = int(os.getenv("GOODFOODS_NUM_CTX", "12288"))

VARIANTS = {
    "goodfoods-llama3.1:8b": {"from": "llama3.1:8b"},
    "goodfoods-qwen3:8b": {"from": "qwen3:8b"},
}


def _installed() -> set[str]:
    models = httpx.get(f"{OLLAMA}/api/tags", timeout=5).json().get("models", [])
    return {m["name"] for m in models} | {m["name"].removesuffix(":latest") for m in models}


def _base_parameters(base: str) -> dict:
    """Carry over the base model's parameters (stop tokens, sampling defaults)."""
    text = httpx.post(f"{OLLAMA}/api/show", json={"model": base}, timeout=10).json().get("parameters", "")
    params: dict = {}
    for line in text.splitlines():
        m = re.match(r"\s*(\w+)\s+(.+?)\s*$", line)
        if not m:
            continue
        key, raw = m.groups()
        value: object = raw.strip('"')
        if re.fullmatch(r"-?\d+", raw):
            value = int(raw)
        elif re.fullmatch(r"-?\d*\.\d+", raw):
            value = float(raw)
        if key == "stop":
            params.setdefault("stop", []).append(value)
        else:
            params[key] = value
    return params


def ensure_variant(name: str) -> str:
    """Create the tuned variant if it's missing. Returns a short status string."""
    spec = VARIANTS[name]
    installed = _installed()
    if name in installed:
        return "present"
    if spec["from"] not in installed:
        return f"base model missing: run `ollama pull {spec['from']}`"
    body = {"model": name, "from": spec["from"], "stream": False,
            "parameters": {**_base_parameters(spec["from"]), "num_ctx": NUM_CTX}}
    resp = httpx.post(f"{OLLAMA}/api/create", json=body, timeout=120)
    resp.raise_for_status()
    return "created"


if __name__ == "__main__":
    for variant in VARIANTS:
        print(f"{variant}: {ensure_variant(variant)}")
