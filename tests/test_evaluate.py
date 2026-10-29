from datetime import datetime, timezone
from types import SimpleNamespace

import openai
import pytest

from newsdesk import evaluate
from newsdesk.llm import BackendUnavailable, LLMResult, get_backend
from newsdesk.llm.openai_compat import OpenAICompatBackend
from newsdesk.models import Item

NOW = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)


def item(id, lang="en", tracks=("aid",)):
    return Item(id=id, url=f"https://x/{id}", title=f"Title {id}", source_id="s", publisher="p",
                publisher_name="P", lang=lang, published=NOW, fetched=NOW, tracks=list(tracks),
                relevance={"aid": 0.6})


def enrichment(on_topic=True, tracks=("aid",), nl_flag=False, hook=False, summary="Donors cut aid."):
    return {"on_topic": on_topic, "tracks": list(tracks) if on_topic else [], "summary_en": summary,
            "type": "news", "region": "Global", "nl_flag": nl_flag,
            "hook": {"flag": hook, "angle": "Why?" if hook else "", "why_now": ""}}


class FakeBackend:
    name, model = "fake", "fake-1"

    def __init__(self, results):
        self.results = results

    def complete_json(self, system, user, schema, max_tokens):
        return self.results[user.split("\n")[0].removeprefix("Title: Title ")]


def test_make_sheet_keeps_labels_and_tops_up(tmp_path):
    path = tmp_path / "eval" / "labels.yaml"
    pool = [item(f"en{n}") for n in range(10)] + [item(f"nl{n}", lang="nl", tracks=("aid", "nl")) for n in range(5)]
    assert evaluate.make_sheet(pool, path, 6) == (6, 6)
    rows = evaluate.load_sheet(path)
    assert sum(r["lang"] == "nl" for r in rows) >= 2  # at least the 30% quota
    assert rows[0]["on_topic"] is None and rows[0]["tracks"] == rows[0]["keyword_tracks"]

    text = path.read_text(encoding="utf-8").replace("\n  on_topic: null", "\n  on_topic: true", 1)
    path.write_text(text, encoding="utf-8")
    assert evaluate.make_sheet(pool, path, 8) == (2, 8)
    rows = evaluate.load_sheet(path)
    assert len({r["id"] for r in rows}) == 8 and len(evaluate.labelled(rows)) == 1


def test_eval_scores_against_labels():
    rows = [
        {"id": "a", "title": "A", "publisher": "P", "lang": "en", "on_topic": True, "tracks": ["aid", "nl"], "hook": True},
        {"id": "b", "title": "B", "publisher": "P", "lang": "en", "on_topic": False, "tracks": [], "hook": False},
        {"id": "c", "title": "C", "publisher": "P", "lang": "nl", "on_topic": True, "tracks": ["aid"], "hook": False},
        {"id": "d", "title": "D", "publisher": "P", "lang": "en", "on_topic": True, "tracks": ["aid"], "hook": None},
    ]
    items = {i: item(i) for i in "abcd"}
    backend = FakeBackend({
        "a": LLMResult("ok", data=enrichment(nl_flag=True, hook=True)),
        "b": LLMResult("ok", data=enrichment(on_topic=False)),
        "c": LLMResult("ok", data=enrichment(on_topic=False, summary="As an AI I cannot comment.")),
        "d": LLMResult("refused", error="refusal"),
    })
    preds, stats, _ = evaluate.run_eval(rows, items, backend)
    assert items["a"].enrichment is None  # store items untouched

    s = evaluate.score(rows, preds)
    assert s["answered"] == 3 and s["on_topic_accuracy"] == pytest.approx(2 / 3)
    assert s["off_topic"]["tp"] == 1 and s["off_topic"]["fp"] == 1  # c wrongly dropped
    assert s["tracks"]["nl"]["recall"] == 1.0 and s["tracks"]["data-ai"]["precision"] is None
    assert s["hook"]["precision"] == 1.0 and s["hook"]["label_rate"] == pytest.approx(1 / 3)
    assert s["disclaimers"] == ["c"]

    text = evaluate.report(rows, preds, stats, 12.0, {}, NOW)
    assert "Refused / invalid / error | 1 / 0 / 0" in text
    assert "**C** (P, nl): on_topic label True, model False; disclaimer" in text
    assert evaluate.report_path(evaluate.Path("e"), {"backend": "ollama", "model": "gemma3:4b"}, NOW).name \
        == "2026-10-29-1200-ollama-gemma3-4b.md"


def test_get_backend_model_override():
    cfg = {"backend": "ollama", "models": {"ollama": {"enrich": "gemma3:4b"}}, "ollama_base_url": "http://x/v1"}
    assert get_backend(cfg).model == "gemma3:4b"
    assert get_backend(cfg, model="qwen3:4b").model == "qwen3:4b"


# --- OpenAI-compatible (Ollama) backend, client mocked ------------------------------

class FakeChat:
    def __init__(self, outputs):
        self.outputs, self.calls = list(outputs), []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        out = self.outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=out, refusal=None))],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20),
        )


def test_ollama_backend_retries_invalid_json_once():
    client = FakeChat(['{"a": ', '{"a": 1}'])
    r = OpenAICompatBackend("m", "http://x", client=client).complete_json("s", "u", {}, 100)
    assert r.status == "ok" and r.data == {"a": 1} and r.usage["input_tokens"] == 200
    assert "not valid JSON" in client.calls[1]["messages"][-1]["content"]

    r = OpenAICompatBackend("m", "http://x", client=FakeChat(["nope", "still no"])).complete_json("s", "u", {}, 100)
    assert r.status == "invalid"


def test_ollama_backend_server_down_is_unavailable():
    down = openai.APIConnectionError(request=SimpleNamespace(method="POST", url="http://x"))
    with pytest.raises(BackendUnavailable):
        OpenAICompatBackend("m", "http://x", client=FakeChat([down])).complete_json("s", "u", {}, 100)
