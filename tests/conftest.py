"""Tests must not depend on the internet, an API key, or cost money.

By default the Groq call used by Phase 2 is replaced with a fake that returns no keywords, so the
code-only parts of the pipeline are tested. To run the old way (real LLM, needs .env + internet):
    set RUN_LIVE_LLM_TESTS=1        (PowerShell:  $env:RUN_LIVE_LLM_TESTS = "1")
"""
import os
import types

import pytest

LIVE = os.getenv("RUN_LIVE_LLM_TESTS") == "1"


@pytest.fixture(autouse=True)
def _no_live_llm(monkeypatch):
    if LIVE:
        return
    monkeypatch.setenv("GROQ_API_KEY", "test-key-never-sent")
    try:
        import resume_std.llm as llm
    except Exception:
        return

    def fake_call(self, messages, max_tokens, json_mode):
        message = types.SimpleNamespace(content='{"items": []}')
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])

    monkeypatch.setattr(llm.GroqLLM, "_call", fake_call)
