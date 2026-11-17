"""Minimal OpenAI-compatible chat-completions client with native tool calling.

One code path serves every provider that speaks ``/v1/chat/completions``: local Ollama, Groq,
OpenRouter, Together, vLLM, Meta's Llama API compatibility endpoint, and so on. Switching models
is configuration, not code.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from goodfoods import config  # noqa: F401  (loads .env before LLM_PROFILE / API keys are read)


@dataclass(frozen=True)
class ModelProfile:
    key: str
    label: str
    base_url: str
    model: str
    api_key_env: str | None = None
    default_key: str = ""
    extra_body: dict = field(default_factory=dict)
    local: bool = False

    @property
    def api_key(self) -> str:
        return os.getenv(self.api_key_env, "") if self.api_key_env else self.default_key


def _custom_profile() -> ModelProfile:
    return ModelProfile("custom", f"Custom · {os.getenv('LLM_MODEL', 'unset')}",
                        os.getenv("LLM_BASE_URL", "http://localhost:8000/v1"), os.getenv("LLM_MODEL", ""),
                        api_key_env="LLM_API_KEY")


OLLAMA = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
PROFILES: dict[str, ModelProfile] = {
    p.key: p for p in [
        ModelProfile("ollama-llama3.1-8b", "Llama 3.1 8B Instruct · Ollama (local)", OLLAMA, "goodfoods-llama3.1:8b",
                     default_key="ollama", local=True),
        ModelProfile("ollama-qwen3-8b", "Qwen3 8B · Ollama (local)", OLLAMA, "goodfoods-qwen3:8b",
                     default_key="ollama", extra_body={"reasoning_effort": "none"}, local=True),
        ModelProfile("groq-llama-3.1-8b", "Llama 3.1 8B Instant · Groq", "https://api.groq.com/openai/v1",
                     "llama-3.1-8b-instant", api_key_env="GROQ_API_KEY"),
        ModelProfile("openrouter-llama-3.3-8b", "Llama 3.3 8B Instruct · OpenRouter", "https://openrouter.ai/api/v1",
                     "meta-llama/llama-3.3-8b-instruct:free", api_key_env="OPENROUTER_API_KEY"),
        _custom_profile(),
    ]
}
DEFAULT_PROFILE = os.getenv("LLM_PROFILE", "ollama-llama3.1-8b")


class LLMError(RuntimeError):
    pass


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # raw JSON string as produced by the model

    def as_message(self) -> dict:
        return {"id": self.id, "type": "function", "function": {"name": self.name, "arguments": self.arguments}}


@dataclass
class LLMResponse:
    content: str
    tool_calls: list[ToolCall]
    prompt_tokens: int
    completion_tokens: int
    latency_ms: float
    finish_reason: str | None


_THINK = re.compile(r"<think>.*?</think>", re.S)


class LLMClient:
    def __init__(self, profile: ModelProfile | str = DEFAULT_PROFILE, *, timeout: float = 120.0,
                 max_retries: int = 3, temperature: float = 0.2, max_tokens: int = 700,
                 seed: int | None = None) -> None:
        self.profile = PROFILES[profile] if isinstance(profile, str) else profile
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_retries = max_retries
        self.seed = seed
        headers = {"Content-Type": "application/json"}
        if self.profile.api_key:
            headers["Authorization"] = f"Bearer {self.profile.api_key}"
        self._http = httpx.Client(base_url=self.profile.base_url.rstrip("/"), headers=headers, timeout=timeout)

    def check(self) -> tuple[bool, str]:
        """Cheap readiness probe for the UI: is the endpoint reachable and the key/model present?"""
        p = self.profile
        if p.api_key_env and not p.api_key:
            return False, f"Set {p.api_key_env} in your environment or .env file."
        try:
            if p.local:
                from goodfoods.agent.ollama_setup import VARIANTS, ensure_variant

                tags = httpx.get(p.base_url.replace("/v1", "") + "/api/tags", timeout=3).json()
                names = {m.get("name") for m in tags.get("models", [])}
                if p.model not in names and f"{p.model}:latest" not in names:
                    if p.model in VARIANTS:  # tuned variant: build it from the base model
                        status = ensure_variant(p.model)
                        if status.startswith("base model missing"):
                            return False, status
                    else:
                        return False, f"Model not pulled. Run: ollama pull {p.model}"
                return True, "Ready (running locally)"
            resp = self._http.get("/models", timeout=5)
            return (resp.status_code < 400), ("Ready" if resp.status_code < 400 else f"HTTP {resp.status_code}")
        except httpx.HTTPError as exc:
            hint = " Is Ollama running? Start it with: ollama serve" if p.local else ""
            return False, f"Can't reach {p.base_url}.{hint} ({exc.__class__.__name__})"

    def chat(self, messages: list[dict], tools: list[dict] | None = None, *, tool_choice: str = "auto",
             temperature: float | None = None) -> LLMResponse:
        body: dict[str, Any] = {
            "model": self.profile.model,
            "messages": messages,
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": self.max_tokens,
            **self.profile.extra_body,
        }
        if self.seed is not None:
            body["seed"] = self.seed
        if tools:
            body["tools"] = tools
            body["tool_choice"] = tool_choice
        data = self._post(body)
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        calls = [ToolCall(id=c.get("id") or f"call_{i}", name=(c.get("function") or {}).get("name", ""),
                          arguments=_as_json_text((c.get("function") or {}).get("arguments")))
                 for i, c in enumerate(message.get("tool_calls") or [])]
        usage = data.get("usage") or {}
        return LLMResponse(content=_THINK.sub("", message.get("content") or "").strip(), tool_calls=calls,
                           prompt_tokens=usage.get("prompt_tokens", 0), completion_tokens=usage.get("completion_tokens", 0),
                           latency_ms=data["_latency_ms"], finish_reason=choice.get("finish_reason"))

    def _post(self, body: dict) -> dict:
        delay = 1.0
        for attempt in range(self.max_retries + 1):
            t0 = time.perf_counter()
            try:
                resp = self._http.post("/chat/completions", json=body)
            except httpx.TimeoutException as exc:
                error = LLMError(f"The model at {self.profile.base_url} timed out.")
                error.__cause__ = exc
            except httpx.HTTPError as exc:
                hint = " Is Ollama running? (ollama serve)" if self.profile.local else ""
                raise LLMError(f"Can't reach the model at {self.profile.base_url}.{hint}") from exc
            else:
                if resp.status_code < 400:
                    data = resp.json()
                    data["_latency_ms"] = (time.perf_counter() - t0) * 1000
                    return data
                detail = resp.text[:300]
                if resp.status_code not in (408, 409, 425, 429, 500, 502, 503, 504):
                    raise LLMError(f"Model API error {resp.status_code}: {detail}")
                error = LLMError(f"Model API busy ({resp.status_code}): {detail}")
                retry_after = resp.headers.get("retry-after")
                if retry_after:
                    try:
                        delay = max(delay, min(float(retry_after), 20.0))
                    except ValueError:
                        pass
            if attempt == self.max_retries:
                raise error
            time.sleep(delay)
            delay *= 2
        raise LLMError("unreachable")  # pragma: no cover


def _as_json_text(arguments: Any) -> str:
    if arguments is None:
        return "{}"
    if isinstance(arguments, str):
        return arguments
    return json.dumps(arguments, ensure_ascii=False)
