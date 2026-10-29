import json

import anthropic

from newsdesk.llm.base import BackendUnavailable, LLMResult


class AnthropicBackend:
    name = "anthropic"

    def __init__(self, model: str, effort: str | None = None, client: anthropic.Anthropic | None = None):
        self.model = model
        self.effort = effort
        self.client = client or anthropic.Anthropic()

    def complete_json(self, system: str, user: str, schema: dict, max_tokens: int) -> LLMResult:
        output_config: dict = {"format": {"type": "json_schema", "schema": schema}}
        if self.effort:
            output_config["effort"] = self.effort
        try:
            resp = self.client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                # Stable system prompt first so it is served from the prompt cache.
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user}],
                output_config=output_config,
            )
        except anthropic.CredentialsError as e:
            raise BackendUnavailable(f"no Anthropic credentials: {e}") from e
        except TypeError as e:
            # Raised by the SDK at request time when no credentials resolve at all.
            if "authentication" in str(e):
                raise BackendUnavailable("no Anthropic credentials (set ANTHROPIC_API_KEY)") from e
            raise
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError, anthropic.NotFoundError) as e:
            raise BackendUnavailable(f"{type(e).__name__}: {e.message}") from e
        except anthropic.APIStatusError as e:
            return LLMResult("error", error=f"{e.status_code}: {e.message}"[:300])
        except anthropic.APIConnectionError as e:
            return LLMResult("error", error=f"connection: {e}"[:300])

        u = resp.usage
        usage = {
            "input_tokens": u.input_tokens,
            "output_tokens": u.output_tokens,
            "cache_read_input_tokens": u.cache_read_input_tokens or 0,
        }
        if resp.stop_reason == "refusal":
            category = resp.stop_details.category if resp.stop_details else None
            return LLMResult("refused", error=f"refusal ({category})", usage=usage)
        if resp.stop_reason == "max_tokens":
            return LLMResult("invalid", error="hit max_tokens", usage=usage)
        text = next((b.text for b in resp.content if b.type == "text"), "")
        try:
            return LLMResult("ok", data=json.loads(text), usage=usage)
        except json.JSONDecodeError as e:
            return LLMResult("invalid", error=f"bad JSON: {e}", usage=usage)
