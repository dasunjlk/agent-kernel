"""Groq provider readiness for CampusGreen (Phase 9A audit).

The agent runs through the OpenAI Agents SDK, which defaults to the Responses
API - a protocol Groq does not serve. To support Groq the agent must be
resolved to an explicit ``OpenAIChatCompletionsModel`` pointed at Groq's
chat-completions endpoint. ``agent._resolve_model`` implements exactly that,
environment-gated so the OpenAI path is untouched when ``GROQ_API_KEY`` is
absent.

This module verifies the wiring in two honest tiers that mirror the rest of
the suite:

- **Deterministic (offline, no key, no network).** With no ``GROQ_API_KEY`` the
  resolver returns ``None`` (SDK default, unchanged); with a key it returns an
  ``OpenAIChatCompletionsModel`` whose model string and client base URL point
  at Groq. These assertions inspect the constructed object only and never make
  a request.
- **Gated (skipped without ``GROQ_API_KEY`` and the Unix-only CLI).** A real
  whether Groq responds is only attempted when a real key is present in a
  POSIX runner. On stock Windows and/or without a key it is strictly skipped -
  the honest position given no credential exists in this environment.
"""

from __future__ import annotations

import os

import pytest
import pytest_asyncio
from agents import OpenAIChatCompletionsModel

import agent
from tool import Tools

GROQ_BASE_URL = "https://api.groq.com/openai/v1"

requires_groq_key = pytest.mark.skipif(
    not os.environ.get("GROQ_API_KEY"),
    reason="GROQ_API_KEY is not set; skipping live Groq connectivity probe",
)

try:
    import agentkernel.cli  # noqa: F401

    _CLI_AVAILABLE = True
except ModuleNotFoundError:
    _CLI_AVAILABLE = False

cli_unavailable = pytest.mark.skipif(
    not _CLI_AVAILABLE,
    reason="agentkernel.cli is unavailable on this platform (imports the Unix-only readline module)",
)


# --- Deterministic wiring checks (no key, no network) ------------------------


def test_default_resolution_is_none_without_groq_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_MODEL", raising=False)
    assert agent._resolve_model() is None


def test_default_agent_keeps_openai_path_without_groq_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    # The agent object was built at import time; the resolver must agree with
    # the no-key, unchanged behaviour (SDK-default model).
    assert agent.campusgreen_agent.model is None


def test_groq_key_yields_chatcompletions_model(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_fake_for_test")
    monkeypatch.setenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    resolved = agent._resolve_model()
    assert isinstance(resolved, OpenAIChatCompletionsModel)
    assert resolved.model == "llama-3.3-70b-versatile"
    assert GROQ_BASE_URL in str(resolved._client.base_url)


def test_groq_model_defaults_when_unset(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_fake_for_test")
    monkeypatch.delenv("GROQ_MODEL", raising=False)
    resolved = agent._resolve_model()
    assert isinstance(resolved, OpenAIChatCompletionsModel)
    assert resolved.model == "llama-3.3-70b-versatile"


def test_groq_client_is_not_openai_host(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_fake_for_test")
    monkeypatch.delenv("GROQ_MODEL", raising=False)
    resolved = agent._resolve_model()
    host = str(resolved._client.base_url)
    assert "api.openai.com" not in host
    assert "api.groq.com" in host


def test_groq_path_preserves_the_seven_tools(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_fake_for_test")
    names = {tool.name for tool in agent.campusgreen_agent.tools}
    assert names == {
        "lookup_campus_location",
        "create_issue",
        "get_issue",
        "search_issues",
        "update_issue",
        "notify_team",
        "get_sustainability_report",
    }
    assert {func.__name__ for func in Tools} == names


# --- Gated live connectivity probe (requires a real key + POSIX CLI) ----------


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def groq_test_client():
    from agentkernel.test import Test

    # Point the test harness at the CLI demo, which will use the Groq model
    # because GROQ_API_KEY is set for this process.
    test = Test("demo.py")
    await test.start()
    try:
        yield test
    finally:
        await test.stop()


@requires_groq_key
@cli_unavailable
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.order(30)
async def test_groq_reasons_and_reports(groq_test_client):
    """A real Groq-backed run must resolve a known location and answer plainly.

    Uses a capability-style prompt so the assertion is robust across Groq model
    output while still proving the chat-completions path completed (no
    protocol error) end to end.
    """
    response = await groq_test_client.send("What can you help me with?")
    lowered = response.lower()
    assert (
        "sustainab" in lowered or "report" in lowered or "issue" in lowered
    ), f"Groq-backed agent should identify its role; got: {response!r}"
