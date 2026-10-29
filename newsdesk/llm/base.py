from dataclasses import dataclass, field
from typing import Literal, Protocol


class BackendUnavailable(Exception):
    """The backend cannot serve any request (no credentials, server down). Stop the batch."""


@dataclass
class LLMResult:
    status: Literal["ok", "refused", "invalid", "error"]
    data: dict | None = None
    error: str | None = None
    usage: dict[str, int] = field(default_factory=dict)  # input_tokens, output_tokens, cache_read_input_tokens


class LLMBackend(Protocol):
    name: str
    model: str

    def complete_json(self, system: str, user: str, schema: dict, max_tokens: int) -> LLMResult:
        """Return JSON conforming to `schema`. Never raises for per-request failures;
        raises BackendUnavailable when no request can succeed."""
        ...
