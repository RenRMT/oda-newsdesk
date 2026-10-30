from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from newsdesk.enrich import SCHEMA, apply_result, enrich, estimate_cost, select_for_enrichment
from newsdesk.llm import BackendUnavailable, LLMResult
from newsdesk.llm.anthropic_backend import AnthropicBackend
from newsdesk.models import Item

NOW = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)
CFG = {"retry_window_days": 2, "max_attempts": 2, "max_items_per_run": 3}
GOOD = {
    "on_topic": True, "tracks": ["aid"], "summary_en": "Donors cut aid.", "type": "news",
    "region": "Global", "nl_flag": True,
    "hook": {"flag": True, "angle": "What next?", "why_now": "Budget week"},
}


def item(id, score=0.6, **kw):
    return Item(id=id, url=f"https://x/{id}", title=f"Title {id}", source_id="s", publisher="p",
                publisher_name="P", lang="en", published=NOW, fetched=kw.pop("fetched", NOW),
                relevance={"aid": score}, tracks=["aid"], **kw)


class FakeBackend:
    name, model = "fake", "fake-1"

    def __init__(self, results):
        self.results = results  # id -> LLMResult | Exception

    def complete_json(self, system, user, schema, max_tokens):
        assert schema is SCHEMA and "Title" in user
        r = self.results[user.split("\n")[0].removeprefix("Title: Title ")]
        if isinstance(r, Exception):
            raise r
        return r


def test_select_filters_and_orders():
    items = [
        item("low", 0.2),
        item("old", 0.9, fetched=NOW - timedelta(days=5)),
        item("done", 0.9, llm_status="ok"),
        item("refused", 0.9, llm_status="refused", llm_attempts=1),
        item("retry", 0.95, llm_status="error", llm_attempts=1),
        item("exhausted", 0.9, llm_status="error", llm_attempts=2),
        item("best", 0.8),
        item("second", 0.7),
        item("third", 0.65),
    ]
    picked = select_for_enrichment(items, NOW, lambda i: 0.4, CFG)
    assert [i.id for i in picked] == ["best", "second", "third"]  # fresh before retries, capped at 3


def test_apply_ok_sets_enrichment_and_tracks():
    i = item("a")
    apply_result(i, LLMResult("ok", data=GOOD), "fake")
    assert i.llm_status == "ok" and i.llm_attempts == 1
    assert i.tracks == ["aid", "nl"]  # nl_flag adds nl
    assert i.enrichment.hook.flag


def test_apply_off_topic_keeps_keyword_tracks_and_hides():
    i = item("a")
    apply_result(i, LLMResult("ok", data=GOOD | {"on_topic": False, "tracks": []}), "fake")
    assert i.tracks == ["aid"] and not i.visible(0.4)


@pytest.mark.parametrize("result,status", [
    (LLMResult("refused", error="refusal"), "refused"),
    (LLMResult("ok", data={"summary_en": "missing fields"}), "invalid"),
    (LLMResult("error", error="503"), "error"),
])
def test_apply_failures_keep_item(result, status):
    i = item("a")
    apply_result(i, result, "fake")
    assert i.llm_status == status and i.enrichment is None and i.visible(0.4)


def test_enrich_collects_stats_and_stops_when_unavailable():
    items = [item("a"), item("b"), item("c")]
    backend = FakeBackend({
        "a": LLMResult("ok", data=GOOD, usage={"input_tokens": 100, "output_tokens": 50}),
        "b": BackendUnavailable("no key"),
        "c": LLMResult("ok", data=GOOD),
    })
    done, stats = enrich(items, backend, concurrency=1)
    assert [i.id for i in done] == ["a"]
    assert stats["ok"] == 1 and stats["input_tokens"] == 100 and "unavailable" in stats
    assert items[1].llm_attempts == 0  # not penalised for an outage


def test_estimate_cost():
    stats = {"model": "m", "input_tokens": 1_000_000, "cache_read_input_tokens": 1_000_000, "output_tokens": 1_000_000}
    assert estimate_cost(stats, {"m": [1.0, 5.0]}) == 6.1
    assert estimate_cost(stats, {}) is None


# --- Anthropic backend response handling (client mocked) ----------------------

def fake_response(stop_reason="end_turn", text='{"a": 1}', category=None):
    return SimpleNamespace(
        stop_reason=stop_reason,
        stop_details=SimpleNamespace(category=category) if stop_reason == "refusal" else None,
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=None),
    )


class FakeClient:
    def __init__(self, response):
        self.messages = SimpleNamespace(create=self._create)
        self.response, self.kwargs = response, None

    def _create(self, **kwargs):
        self.kwargs = kwargs
        return self.response


def test_anthropic_backend_ok_and_request_shape():
    client = FakeClient(fake_response())
    r = AnthropicBackend("claude-haiku-5-5", effort="low", client=client).complete_json("sys", "u", SCHEMA, 100)
    assert r.status == "ok" and r.data == {"a": 1} and r.usage["cache_read_input_tokens"] == 0
    assert client.kwargs["output_config"] == {"format": {"type": "json_schema", "schema": SCHEMA}, "effort": "low"}
    assert client.kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}


def test_anthropic_backend_missing_credentials_is_unavailable():
    class NoAuthClient(FakeClient):
        def _create(self, **kwargs):
            raise TypeError('"Could not resolve authentication method. Expected one of api_key..."')

    with pytest.raises(BackendUnavailable):
        AnthropicBackend("m", client=NoAuthClient(None)).complete_json("s", "u", SCHEMA, 100)


@pytest.mark.parametrize("resp,status", [
    (fake_response("refusal", text="", category="general_harms"), "refused"),
    (fake_response("max_tokens", text='{"a":'), "invalid"),
    (fake_response(text="not json"), "invalid"),
])
def test_anthropic_backend_failure_modes(resp, status):
    r = AnthropicBackend("m", client=FakeClient(resp)).complete_json("s", "u", SCHEMA, 100)
    assert r.status == status


def test_aggregator_is_hidden_like_off_topic():
    i = item("a")
    apply_result(i, LLMResult("ok", data=GOOD | {"aggregator": True}), "fake")
    assert i.llm_status == "ok" and i.enrichment.aggregator
    assert not i.visible(0.4)
    assert "aggregator" in SCHEMA["required"]


def test_ollama_aggregator_flag_is_ignored():
    i = item("a")
    apply_result(i, LLMResult("ok", data=GOOD | {"aggregator": True}), "ollama")
    assert i.enrichment.aggregator and i.visible(0.4)
