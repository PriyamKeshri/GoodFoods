"""Which models the app offers, driven by environment variables or Streamlit Secrets."""

import pytest

from app.ui.runtime import default_profile, profile_keys

ENV_KEYS = ("LLM_PROFILE", "SHOW_LOCAL_MODELS", "GROQ_API_KEY", "OPENROUTER_API_KEY", "LLM_MODEL")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_local_models_by_default():
    assert profile_keys() == ["ollama-llama3.1-8b", "ollama-qwen3-8b"]
    assert default_profile() == "ollama-llama3.1-8b"


def test_hosted_deployment_hides_local_models(monkeypatch):
    monkeypatch.setenv("LLM_PROFILE", "groq-llama-3.1-8b")
    monkeypatch.setenv("SHOW_LOCAL_MODELS", "false")
    assert profile_keys() == ["groq-llama-3.1-8b"]  # listed even before the key is set, so its status explains why
    monkeypatch.setenv("GROQ_API_KEY", "test")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")
    assert profile_keys() == ["groq-llama-3.1-8b", "openrouter-llama-3.3-8b"]


def test_unknown_profile_falls_back_to_the_first_one(monkeypatch):
    monkeypatch.setenv("LLM_PROFILE", "does-not-exist")
    assert default_profile() == "ollama-llama3.1-8b"
