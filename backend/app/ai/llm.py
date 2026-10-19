"""Optional LLM layer, sitting behind the deterministic analyst.

WHY THIS FILE EXISTS AT ALL, GIVEN NO KEY IS CONFIGURED
--------------------------------------------------------
Because the architecture has to be right whether or not a model is reachable.
Right now `OPENAI_API_KEY` is empty and no provider package is installed, so
the analyst is deterministic. That is a good design and it is not going away -
but "we have no LLM" and "we have no idea how an LLM would be wired" are
different states, and only the second one is a design problem.

So this module is the seam. It defines what a provider IS, what it must be
able to do, and what the platform will and will not let it influence. When a
key appears in `.env`, `get_provider()` starts returning a real provider and
exactly one code path changes. When it does not, `get_provider()` returns the
`NullProvider`, which is not a stub that raises - it is the documented,
tested, in-use answer to "what happens with no model".

THE ONE RULE
------------
    THE MODEL MAY WRITE PROSE. IT MAY NOT SUPPLY A FACT.

Everything else in `app/ai/` already decides what is true: `guards` refuses
false premises, `retrieval` supplies citations, `nl_to_sql` selects a
catalogued statement, `sql_validator` refuses unsafe SQL, the session runs
read-only, and `verification` re-reads every number against the rows that came
back. The LLM runs AFTER all of that, on text that has already been verified,
and its output is verified AGAIN by the same gate before it is returned.

That ordering is the whole design. A model that invents "SP had 41,745 orders"
when the row says 39,100 fails `verify_prose` and its answer is discarded - the
verified deterministic answer is returned instead, and the response says which
one it is. A model cannot talk its way past that, because the check does not
read the model's explanation; it re-reads the numbers.

WHAT THE MODEL IS GIVEN
-----------------------
Three things, and nothing else:

  1. the user's question, already screened by `guards`
  2. the deterministic answer - prose composed FROM the returned rows
  3. the rows themselves, JSON, already JSON-safe

Not given: the database connection, the SQL text, the catalogue, the filesystem,
or any tool. There is no tool-calling loop here, and adding one would change
this file from a formatter into an execution surface. If a future requirement
needs the model to choose SQL, it goes through `nl_to_sql` and `sql_validator`
like every other path, not through a function call the model invents.

WHY HTTP AND NOT THE `openai` PACKAGE
--------------------------------------
`urllib` is in the standard library. Making the provider work with a key and
nothing installed means the LLM path can be turned on by adding one line to
`.env`, instead of by adding a dependency that pulls its own transitive tree
into a project whose selling point is that its reads are all verified. Swapping
this for the official SDK later is a change to `_HttpProvider.complete` and
nothing else.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.core.config import get_settings

# The system prompt is deliberately short and deliberately has no persona. A
# long instruction preamble is where a prompt-injection defence usually lives,
# and this defence is structural: the model is not trusted, so there is nothing
# to defend it from. What the prompt does is prevent the most common harmless
# failure - a model that answers "As an AI language model..." instead of
# answering.
SYSTEM_PROMPT = (
    "You rewrite analytics answers for a reader. "
    "You are given a question and an answer that was computed from real query "
    "results. Restate that answer in clear prose. "
    "Use only the numbers in the supplied answer. "
    "Never introduce a figure, a date, a comparison or a conclusion that is not "
    "already there. "
    "Do not add headings, caveats, advice or offers to help. "
    "Answer in at most four sentences. "
    "If the supplied answer does not contain the information, reply exactly: "
    "NO ANSWER AVAILABLE"
)


@dataclass(frozen=True)
class ProviderStatus:
    """What the LLM layer can currently do, and why.

    Reported verbatim by `GET /api/ai/provider` so the answer to "is the LLM
    actually on?" is a measurement of this process rather than a claim in a
    README. `reason` is not decoration: when `available` is false it is the
    exact thing that is missing, which is the only useful thing to tell someone.
    """

    name: str
    available: bool
    reason: str
    model: str | None = None
    #: True when the deterministic analyst produced the answer instead.
    fallback_active: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "available": self.available,
            "reason": self.reason,
            "model": self.model,
            "fallback_active": self.fallback_active,
        }


class LLMProvider(Protocol):
    """What a provider must be able to do.

    One method. A provider that cannot answer returns an empty string rather
    than raising, so a provider outage degrades to the deterministic path
    instead of turning a data question into a 500.
    """

    name: str

    def status(self) -> ProviderStatus: ...

    def complete(self, question: str, context: str, rows_json: str) -> str: ...


class NullProvider:
    """The provider that is always available, because it always declines.

    This is the in-use path today. It is a real class rather than a `None`
    return so that every caller has the same shape to code against, and so the
    unavailable path is exercised by the test suite on every run rather than
    being a branch nobody takes.
    """

    name = "none"

    def status(self) -> ProviderStatus:
        settings = get_settings()
        if not settings.openai_api_key:
            reason = (
                "OPENAI_API_KEY is empty, so no model can be reached. The "
                "deterministic analyst answers every question and is verified "
                "end to end; nothing about it depends on this."
            )
        elif not settings.llm_enabled:
            reason = (
                "LLM_ENABLED is false. Set it to true to allow a configured "
                "provider to restate verified answers."
            )
        else:
            reason = "no provider is configured"
        return ProviderStatus(
            name=self.name,
            available=False,
            reason=reason,
            model=None,
            fallback_active=True,
        )

    def complete(self, question: str, context: str, rows_json: str) -> str:
        return ""


class _HttpProvider:
    """An OpenAI-compatible chat-completions provider over `urllib`.

    "OpenAI-compatible" rather than "OpenAI" because that is the de-facto
    standard: Azure OpenAI, Ollama, vLLM, LM Studio and several gateways all
    speak this shape. Setting `LLM_BASE_URL` is the whole porting effort.
    """

    name = "openai-compatible"

    def __init__(self, api_key: str, model: str, base_url: str,
                 timeout: float, max_tokens: int) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_tokens = max_tokens

    def status(self) -> ProviderStatus:
        return ProviderStatus(
            name=self.name,
            available=True,
            reason=(
                f"configured against {self.base_url} with model "
                f"{self.model!r}. The model may only restate an answer that "
                f"has already been verified against real query rows."
            ),
            model=self.model,
            fallback_active=False,
        )

    def complete(self, question: str, context: str, rows_json: str) -> str:
        """One chat completion. Returns "" on any failure, by design."""
        import urllib.error
        import urllib.request

        payload = json.dumps(
            {
                "model": self.model,
                "max_tokens": self.max_tokens,
                "temperature": 0,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            f"Question:\n{question}\n\n"
                            f"Verified answer:\n{context}\n\n"
                            f"Query rows (JSON):\n{rows_json}"
                        ),
                    },
                ],
            }
        ).encode("utf-8")

        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
            return (body["choices"][0]["message"]["content"] or "").strip()
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError,
                IndexError):
            # A provider that is down, slow, rate-limited or returning
            # something unexpected must not become an error for the caller.
            # The deterministic answer is already in hand and already verified;
            # returning it is strictly better than failing the request.
            return ""


_provider: LLMProvider | None = None


def get_provider(refresh: bool = False) -> LLMProvider:
    """The configured provider, or the one that declines.

    Cached for the life of the process, like `get_settings`: the answer depends
    only on configuration, and rebuilding it per request would mean a provider
    object per question for no benefit. `refresh=True` exists for tests.
    """
    global _provider
    if _provider is not None and not refresh:
        return _provider

    settings = get_settings()
    if settings.llm_enabled and settings.openai_api_key.strip():
        _provider = _HttpProvider(
            api_key=settings.openai_api_key.strip(),
            model=settings.llm_model,
            base_url=settings.llm_base_url,
            timeout=settings.llm_timeout_seconds,
            max_tokens=settings.llm_max_tokens,
        )
    else:
        _provider = NullProvider()
    return _provider


def reset_provider() -> None:
    """Drop the cached provider. Used by tests, and by nothing else."""
    global _provider
    _provider = None


@dataclass
class Enrichment:
    """The result of asking a model to restate a verified answer."""

    #: The text to show. Either the model's, or the deterministic one.
    answer: str
    #: False when the deterministic answer is what is being shown.
    llm_used: bool
    provider: str
    #: Why the model's text was not used, when it was not.
    fallback_reason: str | None = None
    #: Every check the model's text had to pass to be shown.
    checks: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "llm_used": self.llm_used,
            "provider": self.provider,
            "fallback_reason": self.fallback_reason,
            "checks": self.checks,
        }


def enrich(
    question: str,
    verified_answer: str,
    columns: list[str],
    rows: list[dict[str, Any]],
) -> Enrichment:
    """Return prose for a verified answer, from a model when one is available.

    THE DETERMINISTIC ANSWER IS THE DEFAULT AND THE FALLBACK. Three separate
    conditions return it unchanged:

      1. no provider is configured - the state this project is in today
      2. the provider returned nothing, or something that is obviously a
         refusal (`NO ANSWER AVAILABLE`)
      3. the provider's text contains a number that is not in the rows

    The third is the one that matters. The model's prose is checked with
    `verify_prose`, the same function that checks the deterministic answer, so
    there is no second, weaker standard for model-written text. A failure here
    is not an error: it means the model was wrong, and the right response is to
    show the verified text instead.
    """
    from app.ai import verification as verify

    provider = get_provider()
    status = provider.status()

    if not status.available:
        return Enrichment(
            answer=verified_answer,
            llm_used=False,
            provider=status.name,
            fallback_reason=status.reason,
            checks=[{"check": "provider_available", "ok": False,
                     "detail": status.reason}],
        )

    rows_json = json.dumps(rows[:20], default=str)  # noqa: UP031 - rows are JSON-safe already
    drafted = provider.complete(question, verified_answer, rows_json)

    if not drafted:
        return Enrichment(
            answer=verified_answer,
            llm_used=False,
            provider=status.name,
            fallback_reason=(
                "the provider returned no text; the verified deterministic "
                "answer is shown instead"
            ),
            checks=[{"check": "provider_returned_text", "ok": False}],
        )

    if "NO ANSWER AVAILABLE" in drafted.upper():
        return Enrichment(
            answer=verified_answer,
            llm_used=False,
            provider=status.name,
            fallback_reason=(
                "the model reported that the supplied answer did not contain "
                "the information, and was not asked to guess"
            ),
            checks=[{"check": "model_declined", "ok": True}],
        )

    # THE GATE. Identical to the one the deterministic answer passes.
    result = verify.verify_prose(
        drafted, rows, labels=verify.label_values(columns, rows)
    )
    checks: list[dict[str, Any]] = [
        {
            "check": "every_model_number_is_in_the_rows",
            "ok": result.ok,
            "failures": result.failures,
        }
    ]
    if not result.ok:
        return Enrichment(
            answer=verified_answer,
            llm_used=False,
            provider=status.name,
            fallback_reason=(
                f"{len(result.failures)} value(s) written by the model are not "
                f"present in the rows the query returned, so its text was "
                f"discarded and the verified deterministic answer is shown"
            ),
            checks=checks,
        )

    return Enrichment(
        answer=drafted,
        llm_used=True,
        provider=status.name,
        checks=checks,
    )