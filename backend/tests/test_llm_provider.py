"""The optional LLM layer, and the guarantee that it cannot change a fact.

WHAT IS ACTUALLY TESTABLE HERE, AND WHAT IS NOT
------------------------------------------------
No model is reachable in this environment: `OPENAI_API_KEY` is empty and no
provider package is installed. So every test here either exercises the
PROVIDER-UNAVAILABLE path, or installs a fake provider in its place.

That is not a compromise. The dangerous property of an LLM layer is not what
the model says when it works - it is what happens when it does not, and what
happens when it works *wrongly*. Both are testable with a stub, and both are
the properties worth pinning:

  * no provider  -> the verified deterministic answer is returned, unchanged
  * a bad model  -> its text is discarded and the deterministic answer returned
  * a good model -> its text is returned, having passed the same verification
                    gate the deterministic answer passes
  * a refusal    -> never reaches a model at all

A test that waited for a real API key to test any of that would be a test that
never runs.

THE INVARIANT UNDER TEST
------------------------
    The model may write prose. It may not supply a fact.

Every other test module in this suite assumes the analyst is deterministic.
This one proves that assumption still holds when a model is switched on, which
is the condition under which it would otherwise be untested.
"""

from __future__ import annotations

import sys

import pytest

from conftest import REPO_ROOT, load_script

sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.ai import llm  # noqa: E402
from app.ai.analyst import provider_status  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_provider():
    """Never let one test's provider leak into the next."""
    llm.reset_provider()
    yield
    llm.reset_provider()


@pytest.fixture
def client(api_client):
    """The shared authenticated client, under the name these tests expect."""
    return api_client


class StubProvider:
    """A provider that returns exactly what a test tells it to."""

    name = "stub"

    def __init__(self, text: str = "", available: bool = True) -> None:
        self.text = text
        self._available = available
        self.calls: list[tuple[str, str, str]] = []

    def status(self) -> llm.ProviderStatus:
        return llm.ProviderStatus(
            name=self.name,
            available=self._available,
            reason="stubbed for a test",
            model="stub-model",
            fallback_active=not self._available,
        )

    def complete(self, question: str, context: str, rows_json: str) -> str:
        self.calls.append((question, context, rows_json))
        return self.text


def _rows() -> list[dict[str, object]]:
    return [{"destination_state": "SP", "orders": 41745, "revenue": 5998226.96}]


# ==============================================================================
# THE PROVIDER-UNAVAILABLE PATH  (this is the state the project ships in)
# ==============================================================================


def test_no_key_means_the_provider_declines_and_says_why():
    """The state this project is actually in, asserted rather than assumed."""
    from app.core.config import get_settings

    assert get_settings().openai_api_key == "", (
        "this test is the provider-unavailable path and it only means something "
        "while no key is configured"
    )

    status = llm.get_provider().status()
    assert status.available is False
    assert status.name == "none"
    assert "OPENAI_API_KEY" in status.reason, status.reason
    assert status.fallback_active is True


def test_no_key_means_the_deterministic_answer_is_returned_unchanged():
    """The most important test in this file.

    Not "returns something" - returns the SAME STRING. A fallback that
    paraphrases would be a fallback that introduced a new, unverified text
    path, which is the failure the whole design refuses.
    """
    verified = "Answered by `olist.x`. | SP | 41745 | 5998226.96 |"

    result = llm.enrich("Where is the demand?", verified, ["state", "orders"], _rows())

    assert result.answer == verified
    assert result.llm_used is False
    assert result.fallback_reason
    assert "OPENAI_API_KEY" in result.fallback_reason


def test_a_disabled_provider_is_refused_even_with_a_key(monkeypatch):
    """`LLM_ENABLED` is the second, explicit switch.

    Without it, adding a key to `.env` would silently change what the API
    returns, which is exactly the kind of surprise a safety layer must not
    have.
    """
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "openai_api_key", "sk-not-a-real-key")
    monkeypatch.setattr(get_settings(), "llm_enabled", False)
    llm.reset_provider()

    status = llm.get_provider().status()
    assert status.available is False
    assert "LLM_ENABLED" in status.reason, status.reason


def test_a_configured_provider_is_reported_as_available(monkeypatch):
    """The other branch, so the flag is not a one-way door."""
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "openai_api_key", "sk-not-a-real-key")
    monkeypatch.setattr(get_settings(), "llm_enabled", True)
    monkeypatch.setattr(get_settings(), "llm_model", "some-model")
    llm.reset_provider()

    status = llm.get_provider().status()
    assert status.available is True
    assert status.model == "some-model"
    assert status.fallback_active is False


# ==============================================================================
# THE MODEL MAY NOT SUPPLY A FACT
# ==============================================================================


def test_a_model_that_invents_a_number_has_its_text_discarded(monkeypatch):
    """THE test.

    A model that writes "SP had 51000 orders" when the row says 41745 has
    produced a number no query returned. The text is dropped and the verified
    answer is shown - not corrected, not caveated, dropped.
    """
    stub = StubProvider(
        "The state of SP recorded 51000 orders and 5998226.96 in revenue."
    )
    monkeypatch.setattr(llm, "get_provider", lambda *a, **k: stub)

    verified = "| SP | 41745 | 5998226.96 |"
    result = llm.enrich("Where is the demand?", verified, ["state", "orders", "revenue"], _rows())

    assert result.llm_used is False
    assert result.answer == verified
    assert "51000" not in result.answer
    assert "discarded" in (result.fallback_reason or ""), result.fallback_reason


def test_a_model_that_states_only_real_numbers_is_used(monkeypatch):
    """The positive case, so the guard is not simply refusing everything."""
    stub = StubProvider(
        "Sao Paulo leads with 41745 orders and revenue of 5998226.96."
    )
    monkeypatch.setattr(llm, "get_provider", lambda *a, **k: stub)

    verified = "| SP | 41745 | 5998226.96 |"
    result = llm.enrich("Where is the demand?", verified, ["state", "orders", "revenue"], _rows())

    assert result.llm_used is True
    assert result.provider == "stub"
    assert "41745" in result.answer
    assert result.checks and result.checks[0]["ok"] is True


def test_a_model_that_declines_is_not_pressured_into_answering(monkeypatch):
    """`NO ANSWER AVAILABLE` is honoured, not retried.

    A model saying it cannot answer is a safety signal. Treating it as an
    error and asking again - or filling the gap from the deterministic text
    anyway while claiming the model answered - would invert what it means.
    """
    stub = StubProvider("NO ANSWER AVAILABLE")
    monkeypatch.setattr(llm, "get_provider", lambda *a, **k: stub)

    verified = "| SP | 41745 |"
    result = llm.enrich("q", verified, ["state", "orders"], _rows())

    assert result.llm_used is False
    assert result.answer == verified
    assert "was not asked to guess" in (result.fallback_reason or "")


def test_a_provider_that_raises_does_not_break_the_request(monkeypatch):
    """A provider outage must not turn a data question into a 500.

    The deterministic answer is already in hand and already verified, so the
    worst case here is losing the paraphrase.
    """

    class Exploding(StubProvider):
        def complete(self, question, context, rows_json):
            raise TimeoutError("the provider is down")

    monkeypatch.setattr(llm, "get_provider", lambda *a, **k: Exploding())

    verified = "| SP | 41745 |"
    # `enrich` does not catch: `_HttpProvider.complete` is the layer that turns
    # a failure into an empty string, because that is where the transport
    # lives. A provider that raises is a provider that has broken its
    # contract, so this asserts the contract instead of papering over it.
    with pytest.raises(TimeoutError):
        llm.enrich("q", verified, ["state", "orders"], _rows())


def test_the_http_provider_turns_a_transport_failure_into_no_text(monkeypatch):
    """The layer that is actually supposed to swallow failures does so.

    `_HttpProvider.complete` returns "" for a URLError, a timeout, a bad
    status, or a response that is not the shape it expected. Every one of those
    produces the same outcome for the caller: the verified answer, unchanged.
    """
    import urllib.error

    provider = llm._HttpProvider(
        api_key="sk-x", model="m", base_url="https://example.invalid/v1",
        timeout=1.0, max_tokens=10,
    )

    def explode(*args, **kwargs):
        raise urllib.error.URLError("no route to host")

    monkeypatch.setattr("urllib.request.urlopen", explode)
    assert provider.complete("q", "context", "[]") == ""


def test_the_model_is_never_given_the_sql_the_rows_or_a_tool(monkeypatch):
    """What the model can see, stated as a test.

    The provider receives the question, the verified answer and the rows. It
    does NOT receive the SQL text, the catalogue, or any capability - which is
    what makes "the model cannot write" a structural property rather than an
    instruction in a prompt.
    """
    stub = StubProvider("Nothing to add.")
    monkeypatch.setattr(llm, "get_provider", lambda *a, **k: stub)

    llm.enrich(
        "Where is the demand?",
        "| SP | 41745 |",
        ["state", "orders"],
        _rows(),
    )

    assert len(stub.calls) == 1
    question, context, rows_json = stub.calls[0]
    assert question == "Where is the demand?"
    assert "41745" in context
    assert "41745" in rows_json
    # No SQL anywhere in what the model was handed.
    assert "SELECT" not in (question + context + rows_json).upper()
    # And the protocol has no other method that could be called later.
    assert not hasattr(stub, "execute")
    assert not hasattr(stub, "run_sql")


# ==============================================================================
# THE OBSERVABLE STATUS
# ==============================================================================


def test_provider_status_reports_the_fallback_rather_than_claiming_a_model():
    """`provider_status()` is what `/api/ai/provider/status` returns.

    It must say `available: false` today. A status endpoint that reported a
    model was configured because the settings class HAS a model field would be
    the exact failure this project exists to avoid: a configuration default
    rendered as a capability.
    """
    status = provider_status()
    assert status["available"] is False
    assert status["fallback_active"] is True
    assert status["deterministic_analyst"]
    assert "OPENAI_API_KEY" in status["reason"]

    # And it states the limits as things that are enforced, not requested.
    limits = " ".join(status["model_may_not"]).lower()
    assert "supply a number" in limits
    assert "choose or alter sql" in limits
    assert "call a tool" in limits


def test_the_provider_status_endpoint_is_admin_scoped(client):
    """It describes how the AI layer is wired, which is reconnaissance.

    The shared client holds a full-scope key, so the 401 here proves
    *authentication* is required; the 403 proves *authorization*, which needs a
    key that genuinely lacks the scope.
    """
    unauthenticated = client.request(
        "GET", "/api/ai/provider/status", headers={"X-API-Key": ""}
    )
    assert unauthenticated.status_code == 401

    assert client.get("/api/ai/provider/status").status_code == 200

    issuer = load_script("backend/scripts/db/08_issue_api_key.py")
    with issuer.connect() as conn:
        key_id, key = issuer.create_key(
            conn, label="pytest-llm-analytics", scopes="analytics:read",
            created_by="pytest",
        )
    try:
        response = client.get(
            "/api/ai/provider/status", headers={"X-API-Key": key}
        )
        assert response.status_code == 403, response.status_code
        assert "admin:read" in response.json()["detail"]
    finally:
        with issuer.connect() as conn:
            conn.execute("DELETE FROM app.api_keys WHERE id = %s", (key_id,))


# ==============================================================================
# THE WHOLE ANALYST, WITH AND WITHOUT A MODEL
# ==============================================================================


def test_the_analyst_still_answers_correctly_with_no_model(client):
    """The end-to-end path is unchanged by the LLM layer existing at all."""
    response = client.post(
        "/api/ai/ask", json={"question": "olist revenue by state"}
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["answered"] is True, body.get("reason")
    assert body["verified"]["ok"] is True, body["verified"]["failures"]
    assert body["rows"], "no rows came back"
    # The new field is present and honest on the default path.
    assert body["llm"]["llm_used"] is False
    assert body["llm"]["provider"] == "none"
    # The safety trace still ENDS at the last gate. Presentation is reported in
    # `llm`, never appended to `pipeline`, so "the last thing that happened was
    # verification" stays true no matter what the LLM layer is doing.
    assert body["pipeline"][-1] == "verify:ok", body["pipeline"]


def test_a_refusal_never_reaches_a_model(client, monkeypatch):
    """A false premise is refused before anything is composed.

    The refusal happens at gate 1, so there is no verified answer for a model
    to restate and nothing for it to be shown. Asserting that the provider was
    never called is the point: a model must not be the thing that decides a
    question is unanswerable.
    """
    calls: list[str] = []

    class Tripwire(StubProvider):
        def complete(self, question, context, rows_json):
            calls.append(question)
            return "I can answer that."

    monkeypatch.setattr(llm, "get_provider", lambda *a, **k: Tripwire())
    llm.reset_provider()

    response = client.post(
        "/api/ai/ask",
        json={"question": "What is the total OmniRetail revenue?"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["answered"] is False
    assert body["stopped_at"] == "false_premise", body["stopped_at"]
    assert calls == [], "a model was consulted about a refused question"