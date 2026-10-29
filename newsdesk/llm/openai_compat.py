import json

import openai

from newsdesk.llm.base import BackendUnavailable, LLMResult


class OpenAICompatBackend:
    """Local models served by Ollama or llama.cpp through their OpenAI-compatible API."""

    name = "ollama"

    def __init__(self, model: str, base_url: str, client: openai.OpenAI | None = None):
        self.model = model
        self.client = client or openai.OpenAI(base_url=base_url, api_key="unused", timeout=300)

    def _call(self, messages: list[dict], schema: dict, max_tokens: int):
        return self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=0,
            response_format={"type": "json_schema", "json_schema": {"name": "result", "schema": schema, "strict": True}},
        )

    def complete_json(self, system: str, user: str, schema: dict, max_tokens: int) -> LLMResult:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        usage = {"input_tokens": 0, "output_tokens": 0}
        # Small local models occasionally emit invalid JSON; retry once with the parse error.
        for attempt in range(2):
            try:
                resp = self._call(messages, schema, max_tokens)
            except openai.APIConnectionError as e:
                raise BackendUnavailable(f"cannot reach local model server: {e}") from e
            except openai.NotFoundError as e:
                raise BackendUnavailable(f"model not available: {e}") from e
            except openai.APIError as e:
                return LLMResult("error", error=str(e)[:300], usage=usage)
            if resp.usage:
                usage["input_tokens"] += resp.usage.prompt_tokens
                usage["output_tokens"] += resp.usage.completion_tokens
            msg = resp.choices[0].message
            if getattr(msg, "refusal", None):
                return LLMResult("refused", error=msg.refusal[:300], usage=usage)
            try:
                return LLMResult("ok", data=json.loads(msg.content or ""), usage=usage)
            except json.JSONDecodeError as e:
                if attempt == 1:
                    return LLMResult("invalid", error=f"bad JSON: {e}", usage=usage)
                messages += [
                    {"role": "assistant", "content": msg.content or ""},
                    {"role": "user", "content": f"That was not valid JSON ({e}). Reply with only the JSON object."},
                ]
